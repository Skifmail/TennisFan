"""Идемпотентная оплата детских заявок через существующий шлюз ЮKassa."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import requests
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.payments.yookassa_client import (
    _create_auth,
    create_payment,
    get_payment_details,
)
from apps.users.models import User

from .competition import eligibility
from .models import (
    AuditEvent,
    ChildProfile,
    JuniorEntry,
    JuniorPayment,
    JuniorRefund,
    JuniorTournament,
)
from .notifications import notify_representative
from .permissions import guardian_for, require_organizer


def begin_payment(*, actor: User, entry_id: int, return_url: str) -> JuniorPayment:
    """Создаёт попытку с устойчивым ключом, повторный запрос не создаёт второй платёж."""
    if not settings.JUNIOR_PAYMENTS_ENABLED:
        raise ValidationError("Онлайн-оплата детской лиги пока не открыта.")
    with transaction.atomic():
        entry = JuniorEntry.objects.select_related("child", "tournament").get(
            pk=entry_id
        )
        tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
            pk=entry.tournament_id
        )
        child = ChildProfile.objects.select_for_update(of=("self",)).get(
            pk=entry.child_id
        )
        link = guardian_for(actor, child)
        entry = JuniorEntry.objects.select_for_update(of=("self",)).get(pk=entry_id)
        eligibility(child, link, tournament)
        if entry.guardian.user_id != actor.pk:
            raise PermissionDenied("Оплату оформляет представитель, подавший заявку.")
        if (
            entry.status != "payment"
            or not entry.reserved_until
            or entry.reserved_until <= timezone.now()
            or tournament.status != "open"
            or tournament.registration_ends_at <= timezone.now()
        ):
            raise ValidationError(
                "Резерв истёк или регистрация закрыта. Не оплачивайте эту заявку."
            )
        payment, _ = JuniorPayment.objects.get_or_create(
            entry=entry,
            reservation=entry.reserved_until,
            defaults={
                "payer": actor,
                "amount": entry.amount,
                "return_url": return_url,
                "receipt_email": actor.email,
            },
        )
        if payment.status not in ("new", "pending"):
            raise ValidationError("Эта попытка оплаты уже завершена.")
        if payment.provider_id:
            return cast(JuniorPayment, payment)
    return cast(JuniorPayment, issue_payment(payment))


def issue_payment(payment: JuniorPayment) -> JuniorPayment:
    """Восстанавливает потерянный ответ в пределах срока идемпотентности шлюза."""
    if payment.created_at < timezone.now() - timedelta(hours=23):
        raise ValidationError(
            "Истёк безопасный срок повтора. Не создавайте повторный платёж; требуется сверка оператором."
        )
    provider_id, url = create_payment(
        amount=f"{payment.amount:.2f}",
        return_url=payment.return_url,
        description=f"Детская лига TennisFan: заявка №{payment.entry_id}",
        metadata={"payment_type": "junior", "junior_payment_id": str(payment.pk)},
        customer_email=payment.receipt_email,
        idempotency_key=str(payment.pk),
    )
    # Ответ шлюза не содержит ФИО, дату рождения или документы ребёнка.
    with transaction.atomic():
        payment = JuniorPayment.objects.select_for_update(of=("self",)).get(
            pk=payment.pk
        )
        payment.provider_id = provider_id
        payment.confirmation_url = url
        if payment.status == "new":
            payment.status = "pending"
        payment.save(update_fields=["provider_id", "confirmation_url", "status"])
    return cast(JuniorPayment, payment)


def reconcile_payment(payment_id: Any) -> JuniorPayment:
    """Перепроверяет статус в API шлюза; входящее уведомление само по себе не является оплатой."""
    payment = JuniorPayment.objects.get(pk=payment_id)
    if not payment.provider_id:
        raise ValidationError("Платёж ещё создаётся.")
    details = get_payment_details(payment.provider_id)
    if not details:
        raise ValidationError("Не удалось проверить платёж. Повторите позже.")
    try:
        valid = (
            details["id"] == payment.provider_id
            and Decimal(details["amount"]["value"]) == payment.amount
            and details["amount"]["currency"] == "RUB"
            and details["metadata"]["junior_payment_id"] == str(payment.pk)
        )
    except (KeyError, TypeError, InvalidOperation):
        valid = False
    if not valid:
        raise ValidationError("Данные платежа не совпадают с заявкой.")
    with transaction.atomic():
        entry = JuniorEntry.objects.get(pk=payment.entry_id)
        tournament = JuniorTournament.objects.select_for_update(of=("self",)).get(
            pk=entry.tournament_id
        )
        child = ChildProfile.objects.select_for_update(of=("self",)).get(
            pk=entry.child_id
        )
        entry = JuniorEntry.objects.select_for_update(of=("self",)).get(pk=entry.pk)
        payment = JuniorPayment.objects.select_for_update(of=("self",)).get(
            pk=payment.pk
        )
        if payment.status in ("paid", "refund_pending", "refunded"):
            return cast(JuniorPayment, payment)
        if details.get("status") == "canceled":
            payment.status = "cancelled"
        elif details.get("status") == "succeeded" and details.get("paid") is True:
            valid_entry = (
                entry.status == "payment"
                and entry.reserved_until == payment.reservation
                and payment.reservation > timezone.now()
                and tournament.status in ("open", "closed")
                and tournament.starts_at > timezone.now()
            )
            try:
                link = guardian_for(payment.payer, child)
                eligibility(child, link, tournament)
            except (PermissionDenied, ValidationError):
                valid_entry = False
            payment.status = "paid" if valid_entry else "refund_pending"
            if valid_entry:
                entry.status = "confirmed"
                entry.save(update_fields=["status"])
            else:
                JuniorRefund.objects.get_or_create(
                    payment=payment,
                    defaults={
                        "reason": "Оплата после истечения резерва, снятия или изменения допуска"
                    },
                )
            AuditEvent.objects.create(
                actor=payment.payer,
                child=child,
                action=(
                    "payment.confirmed" if valid_entry else "payment.refund_required"
                ),
                details={"payment_id": str(payment.pk)},
            )
            notify_representative(
                payment.payer,
                child,
                (
                    "Взнос подтверждён."
                    if valid_entry
                    else "Оплата требует возврата. Запрос создан автоматически."
                ),
            )
        payment.save(update_fields=["status"])
    return cast(JuniorPayment, payment)


@transaction.atomic
def request_refund(*, actor: User, payment_id: Any, reason: str) -> JuniorRefund:
    """Сохраняет запрос исходного плательщика, не меняя получателя средств."""
    payment = (
        JuniorPayment.objects.select_for_update(of=("self",))
        .select_related("entry__tournament")
        .get(pk=payment_id)
    )
    if payment.payer_id != actor.pk:
        raise PermissionDenied("Возврат запрашивает исходный плательщик.")
    if payment.status not in ("paid", "refund_pending"):
        raise ValidationError("Нет подтверждённой оплаты для возврата.")
    if (
        payment.entry.status != "withdrawn"
        and payment.entry.tournament.status != "cancelled"
        and payment.status != "refund_pending"
    ):
        raise ValidationError(
            "Сначала снимите заявку; после старта обратитесь к организатору."
        )
    if not reason.strip():
        raise ValidationError("Укажите причину возврата.")
    refund, _ = JuniorRefund.objects.get_or_create(
        payment=payment, defaults={"reason": reason.strip()[:255]}
    )
    return cast(JuniorRefund, refund)


def execute_refund(*, actor: User, refund_id: int) -> JuniorRefund:
    """Выполняет полный возврат через шлюз после решения ответственного организатора."""
    with transaction.atomic():
        refund = (
            JuniorRefund.objects.select_for_update(of=("self",))
            .select_related("payment__entry__tournament")
            .get(pk=refund_id)
        )
        payment = refund.payment
        require_organizer(actor, payment.entry.tournament)
        if refund.status == "refunded":
            return cast(JuniorRefund, refund)
        if payment.status not in ("paid", "refund_pending") or not payment.provider_id:
            raise ValidationError("Оплата не допускает возврат.")
        if (
            refund.started_at
            and not refund.provider_id
            and refund.started_at < timezone.now() - timedelta(hours=23)
        ):
            raise ValidationError(
                "Неопределённый возврат требует ручной сверки со шлюзом. Повторное списание не выполняется."
            )
        refund.started_at = refund.started_at or timezone.now()
        refund.status = "processing"
        refund.approved_by = actor
        refund.save(update_fields=["status", "approved_by", "started_at"])
    payload: dict[str, Any] = {
        "payment_id": payment.provider_id,
        "amount": {"value": f"{payment.amount:.2f}", "currency": "RUB"},
        "description": "Возврат взноса детской лиги",
        "receipt": {
            "customer": {"email": payment.receipt_email or payment.payer.email},
            "items": [
                {
                    "description": "Возврат взноса детской лиги",
                    "quantity": "1.00",
                    "amount": {"value": f"{payment.amount:.2f}", "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }
            ],
        },
    }
    if refund.provider_id:
        response = requests.get(
            f"https://api.yookassa.ru/v3/refunds/{refund.provider_id}",
            auth=_create_auth(),
            timeout=15,
        )
    else:
        response = requests.post(
            "https://api.yookassa.ru/v3/refunds",
            auth=_create_auth(),
            headers={"Idempotence-Key": str(refund.key)},
            json=payload,
            timeout=15,
        )
    response.raise_for_status()
    result = response.json()
    if (
        result.get("payment_id") != payment.provider_id
        or result.get("amount") != payload["amount"]
    ):
        raise ValidationError("Некорректный ответ шлюза о возврате.")
    with transaction.atomic():
        refund = JuniorRefund.objects.select_for_update(of=("self",)).get(pk=refund.pk)
        refund.provider_id = result["id"]
        if result.get("status") == "succeeded":
            refund.status = "refunded"
            JuniorPayment.objects.filter(pk=payment.pk).update(status="refunded")
        refund.save(update_fields=["provider_id", "status"])
        AuditEvent.objects.create(
            actor=actor,
            child=payment.entry.child,
            action="refund.updated",
            details={"refund_id": refund.pk, "status": refund.status},
        )
        notify_representative(
            payment.payer,
            payment.entry.child,
            "Возврат взноса обработан. Статус — в истории оплат.",
        )
    return cast(JuniorRefund, refund)
