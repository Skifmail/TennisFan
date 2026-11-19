"""Выбор способа взноса, учёт оплаты на корте и сохранение финансовой истории."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, cast

from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from loguru import logger

from apps.clubs.models import (
    ClubMember,
    ClubMemberBalanceTransaction,
    ClubMemberPlan,
    ClubMemberStatus,
)
from apps.clubs.plan_services import (
    RegistrationMode,
    check_tournament_registration_eligibility,
    consume_member_tournament_limit,
)
from apps.subscriptions.fancoin import TOURNAMENT_REGISTRATION_COST
from apps.subscriptions.models import FancoinTransaction, UserSubscription

from .models import (
    Tournament,
    TournamentEntryRefundRequest,
    TournamentOnsitePayment,
    TournamentPostpaymentInvoice,
    TournamentRegistrationCoverage,
    TournamentStatus,
)

if TYPE_CHECKING:
    from apps.users.models import User


class RegistrationPaymentError(ValueError):
    """Недопустимый способ взноса или изменившиеся условия регистрации."""


class RegistrationPaymentMethod(models.TextChoices):
    """Способы регистрации, которые сервер проверяет независимо от формы и сессии."""

    COVERED = "covered", "По тарифу"
    PAID = "paid", "Взнос уже оплачен"
    FREE = "free", "Без взноса"
    ADMIN = "admin", "Регистрация администратора"
    ONSITE = "onsite", "На корте"
    POSTPAYMENT = "postpayment", "Постоплата"
    ONLINE = "online", "Оплатить онлайн"


@dataclass(frozen=True, slots=True)
class RegistrationPaymentOption:
    """Доступный способ взноса и понятное объяснение его последствий."""

    value: str
    label: str
    description: str


def active_onsite_payments(tournament: Tournament) -> QuerySet[TournamentOnsitePayment]:
    """Вернуть действующие ожидания и реально полученные взносы, не удаляя историю."""
    return cast(
        QuerySet[TournamentOnsitePayment],
        tournament.onsite_payments.filter(
            status__in=[
                TournamentOnsitePayment.Status.EXPECTED,
                TournamentOnsitePayment.Status.RECEIVED,
            ]
        ),
    )


def has_onsite_payment(tournament: Tournament, user: User) -> bool:
    """Проверить выбранную оплату на корте, не выдавая ожидание за получение денег."""
    return bool(active_onsite_payments(tournament).filter(user=user).exists())


def has_received_entry_payment(tournament: Tournament, user: User) -> bool:
    """Проверить именно факт оплаты: онлайн, постоплата или подтверждённый взнос на корте."""
    return bool(
        user.pk in get_paid_entry_user_ids(tournament)
        or tournament.onsite_payments.filter(
            user=user, status=TournamentOnsitePayment.Status.RECEIVED
        ).exists()
    )


def get_paid_entry_user_ids(tournament: Tournament) -> set[int]:
    """Отделить действующую онлайн-оплату от сохранённого факта уже возвращённых денег."""
    from apps.payments.models import PaymentRecord

    paid_times = dict(tournament.entry_payments.values_list("user_id", "paid_at"))
    for user_id, paid_at in tournament.postpayment_invoices.filter(
        status=TournamentPostpaymentInvoice.Status.PAID
    ).values_list("user_id", "paid_at"):
        previous = paid_times.get(user_id)
        if previous is None or (paid_at is not None and paid_at > previous):
            paid_times[user_id] = paid_at
    if not tournament.club_id or not paid_times:
        return set(paid_times)

    # Заявка на ручной возврат не означает возврата. Доказательство — завершённая
    # операция зачисления на баланс именно этого клуба, турнира и пользователя.
    refunds = ClubMemberBalanceTransaction.objects.filter(
        club_id=tournament.club_id,
        member__user_id__in=paid_times,
        source=ClubMemberBalanceTransaction.Source.TOURNAMENT_REFUND,
        direction=ClubMemberBalanceTransaction.Direction.CREDIT,
        status=ClubMemberBalanceTransaction.Status.COMPLETED,
    ).filter(
        Q(reference=f"tournament:{tournament.pk}")
        | Q(metadata__tournament_id=tournament.pk)
    )
    returned_at: dict[int, datetime] = {}
    for user_id, completed_at, created_at in refunds.values_list(
        "member__user_id", "completed_at", "created_at"
    ):
        returned = completed_at or created_at
        if user_id not in returned_at or returned > returned_at[user_id]:
            returned_at[user_id] = returned
    if returned_at:
        for record in PaymentRecord.objects.filter(
            user_id__in=returned_at,
            payment_type=PaymentRecord.PaymentType.TOURNAMENT,
            item_id=str(tournament.pk),
            status="succeeded",
        ):
            received_at = (
                parse_datetime(
                    str(
                        (record.metadata or {}).get(
                            "_tournament_entry_finalized_at", ""
                        )
                    )
                )
                or record.paid_at
            )
            previous = paid_times.get(record.user_id)
            if previous is None or received_at > previous:
                paid_times[record.user_id] = received_at
    return {
        user_id
        for user_id, paid_at in paid_times.items()
        if user_id not in returned_at
        or (paid_at is not None and paid_at > returned_at[user_id])
    }


def get_tournament_entry_amount(tournament: Tournament, user: User) -> Decimal:
    """Рассчитать рублёвый взнос одинаково для онлайн-оплаты и оплаты на площадке."""
    amount = Decimal(tournament.entry_fee or 0)
    if not tournament.club_id:
        subscription = (
            UserSubscription.objects.select_related("tier").filter(user=user).first()
        )
        if subscription is not None and subscription.is_valid():
            percent = min(subscription.tier.one_day_tournament_discount, 100)
            amount *= (Decimal(100) - Decimal(percent)) / Decimal(100)
    return max(amount, Decimal("0")).quantize(Decimal("0.01"))


def get_registration_payment_options(
    tournament: Tournament, user: User
) -> list[RegistrationPaymentOption]:
    """Предложить только разрешённые способы без списаний и создания заявок."""
    from .postpayment import (
        _can_pay_entry_with_fancoin,
        tournament_allows_postpayment_registration,
    )

    if has_received_entry_payment(tournament, user):
        return [
            RegistrationPaymentOption(
                "paid", "Взнос уже оплачен", "Повторная оплата не нужна."
            )
        ]
    if tournament.registration_coverages.filter(user=user).exists():
        return [
            RegistrationPaymentOption(
                "covered", "Участие уже покрыто", "Повторного списания не будет."
            )
        ]
    if user.is_staff or user.is_superuser:
        return [
            RegistrationPaymentOption(
                "admin", "Регистрация администратора", "Без взноса и списания лимита."
            )
        ]

    fee = Decimal(tournament.entry_fee or 0)
    options: list[RegistrationPaymentOption] = []
    member = (
        ClubMember.objects.select_related("club")
        .filter(club_id=tournament.club_id, user=user, status=ClubMemberStatus.ACTIVE)
        .first()
        if tournament.club_id
        else None
    )
    if member is not None:
        eligibility = check_tournament_registration_eligibility(member, tournament)
        if eligibility.mode == RegistrationMode.BLOCKED:
            raise RegistrationPaymentError(eligibility.message)
        if eligibility.mode == RegistrationMode.FREE:
            if eligibility.consumes_limit:
                options.append(
                    RegistrationPaymentOption(
                        "covered",
                        "Клубный тариф",
                        "Будет использована одна регистрация клубного тарифа.",
                    )
                )
            elif fee <= 0:
                options.append(
                    RegistrationPaymentOption(
                        "free", "Без взноса", "Лимит клубного тарифа не расходуется."
                    )
                )
    elif _can_pay_entry_with_fancoin(tournament):
        subscription = (
            UserSubscription.objects.select_related("tier").filter(user=user).first()
        )
        if subscription is not None and subscription.has_fancoin(
            TOURNAMENT_REGISTRATION_COST
        ):
            options.append(
                RegistrationPaymentOption(
                    "covered",
                    f"{TOURNAMENT_REGISTRATION_COST} FT",
                    "Списание с баланса FT при регистрации.",
                )
            )
    elif fee <= 0 and tournament.is_one_day:
        options.append(
            RegistrationPaymentOption(
                "free", "Без взноса", "Бесплатная регистрация без списаний."
            )
        )

    if fee > 0:
        amount = get_tournament_entry_amount(tournament, user)
        if tournament.allow_onsite_payment:
            options.append(
                RegistrationPaymentOption(
                    "onsite",
                    "На корте",
                    f"{amount} ₽ организатору на площадке. Получение денег он подтвердит отдельно.",
                )
            )
        if tournament_allows_postpayment_registration(tournament):
            options.append(
                RegistrationPaymentOption(
                    "postpayment",
                    "Постоплата",
                    "Ссылка и срок оплаты появятся после открытия окна постоплаты. До этого платить не нужно.",
                )
            )
        else:
            options.append(
                RegistrationPaymentOption(
                    "online",
                    "Оплатить онлайн",
                    f"{amount} ₽ до завершения регистрации.",
                )
            )
    if not options:
        raise RegistrationPaymentError(
            "Для участия нужна подписка или клубный тариф с доступным лимитом."
        )
    return options


def resolve_registration_payment_method(
    tournament: Tournament, user: User, value: str | None
) -> RegistrationPaymentMethod:
    """Проверить POST по актуальным условиям; старым формам оставить безопасный выбор."""
    options = get_registration_payment_options(tournament, user)
    if not value:
        if (
            tournament.allow_onsite_payment
            and (tournament.entry_fee or 0) > 0
            and len(options) > 1
        ):
            raise RegistrationPaymentError("Выберите способ оплаты взноса.")
        value = options[0].value
    if value not in {option.value for option in options}:
        raise RegistrationPaymentError(
            "Этот способ оплаты недоступен. Выберите один из предложенных вариантов."
        )
    return RegistrationPaymentMethod(value)


def lock_registration_payment_users(tournament: Tournament, users: list[User]) -> None:
    """Зафиксировать балансы и клубные тарифы обоих игроков до первого списания."""
    user_ids = sorted({user.pk for user in users})
    list(
        get_user_model()
        .objects.select_for_update()
        .filter(pk__in=user_ids)
        .order_by("pk")
    )
    list(
        UserSubscription.objects.select_for_update()
        .filter(user_id__in=user_ids)
        .order_by("pk")
    )
    if tournament.club_id:
        list(
            ClubMember.objects.select_for_update()
            .filter(club_id=tournament.club_id, user_id__in=user_ids)
            .order_by("pk")
        )
        list(
            ClubMemberPlan.objects.select_for_update()
            .filter(
                club_member__club_id=tournament.club_id,
                club_member__user_id__in=user_ids,
            )
            .order_by("pk")
        )


@transaction.atomic
def apply_registration_payment(
    tournament: Tournament, user: User, method: RegistrationPaymentMethod
) -> None:
    """Зафиксировать выбранный способ внутри транзакции создания участника или пары."""
    # Повторная проверка обязательна: между показом формы и POST условия могли измениться.
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    lock_registration_payment_users(tournament, [user])
    resolve_registration_payment_method(tournament, user, method.value)
    if method == RegistrationPaymentMethod.ONLINE:
        raise RegistrationPaymentError("Сначала оплатите вступительный взнос онлайн.")
    if method in (RegistrationPaymentMethod.PAID, RegistrationPaymentMethod.FREE):
        cancel_pending_entry_invoice(tournament, user)
        return
    if method == RegistrationPaymentMethod.ONSITE:
        if not has_onsite_payment(tournament, user):
            TournamentOnsitePayment.objects.create(
                tournament=tournament,
                user=user,
                amount=get_tournament_entry_amount(tournament, user),
            )
        cancel_pending_entry_invoice(tournament, user)
        return
    if method == RegistrationPaymentMethod.POSTPAYMENT:
        return
    if method == RegistrationPaymentMethod.ADMIN:
        coverage_type = TournamentRegistrationCoverage.CoverageType.ADMIN_GRANTED
    else:
        if tournament.registration_coverages.filter(user=user).exists():
            cancel_pending_entry_invoice(tournament, user)
            return
        member = (
            ClubMember.objects.select_related("club")
            .filter(
                club_id=tournament.club_id, user=user, status=ClubMemberStatus.ACTIVE
            )
            .first()
            if tournament.club_id
            else None
        )
        if member is not None:
            eligibility = check_tournament_registration_eligibility(member, tournament)
            if (
                eligibility.mode != RegistrationMode.FREE
                or not eligibility.consumes_limit
            ):
                raise RegistrationPaymentError(
                    "Лимит клубного тарифа больше недоступен."
                )
            consumed, error = consume_member_tournament_limit(member, tournament)
            if not consumed:
                raise RegistrationPaymentError(error)
            coverage_type = TournamentRegistrationCoverage.CoverageType.CLUB_PLAN_SLOT
        else:
            subscription = (
                UserSubscription.objects.select_related("tier")
                .filter(user=user)
                .first()
            )
            if subscription is None or not subscription.spend_fancoin(
                TOURNAMENT_REGISTRATION_COST,
                reason=FancoinTransaction.Reason.TOURNAMENT_REGISTRATION,
                tournament=tournament,
            ):
                raise RegistrationPaymentError(
                    "На балансе недостаточно FT для регистрации."
                )
            coverage_type = (
                TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT
            )
    TournamentRegistrationCoverage.objects.get_or_create(
        tournament=tournament, user=user, defaults={"coverage_type": coverage_type}
    )
    cancel_pending_entry_invoice(tournament, user)


def get_registration_closed_reason(tournament: Tournament) -> str | None:
    """Проверить закрытие набора одинаково для одиночной регистрации, пары и callback."""
    if tournament.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED):
        return "Регистрация недоступна: турнир уже завершён или отменён."
    if tournament.bracket_generated:
        return "Регистрация закрыта: сетка турнира уже сформирована."
    if tournament.status != TournamentStatus.UPCOMING:
        return "Регистрация закрыта: турнир уже начался."
    if tournament.postpayment_window_started_at is not None:
        return "Регистрация закрыта: запущено окно постоплаты и формирование сетки."
    if tournament.is_full():
        return "Регистрация закрыта: все места заняты."
    if not tournament.start_after_fill:
        now = timezone.now()
        deadline = tournament.registration_deadline
        if deadline is not None:
            if timezone.is_naive(deadline):
                deadline = timezone.make_aware(deadline)
            if deadline <= now:
                return "Регистрация закрыта: срок подачи заявки истёк."
        start = tournament.get_start_datetime()
        if (start is not None and start <= now) or (
            start is None
            and tournament.start_date is not None
            and tournament.start_date < timezone.localdate()
        ):
            return "Регистрация закрыта: турнир уже начался."
    return None


def cancel_pending_entry_invoice(tournament: Tournament, user: User) -> None:
    """Закрыть старый запрос постоплаты, не удаляя счёт и его финансовую историю."""
    tournament.postpayment_invoices.filter(
        user=user, status=TournamentPostpaymentInvoice.Status.PENDING
    ).update(status=TournamentPostpaymentInvoice.Status.CANCELLED)


def user_is_registered(tournament: Tournament, user: User) -> bool:
    """Проверить участие, включая игрока парной команды без партнёра."""
    return bool(
        tournament.participants.filter(user=user).exists()
        or (
            tournament.is_doubles()
            and tournament.teams.filter(
                Q(player1__user=user) | Q(player2__user=user)
            ).exists()
        )
    )


@transaction.atomic
def confirm_onsite_payment(
    tournament: Tournament, payment_id: int, *, received_by: User
) -> bool:
    """Однократно подтвердить получение денег; права проверяются вызывающей страницей."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    payment = TournamentOnsitePayment.objects.select_for_update().get(
        pk=payment_id, tournament=tournament
    )
    if payment.status == TournamentOnsitePayment.Status.RECEIVED:
        return False
    from .withdraw import is_player_withdrawn

    if (
        payment.status != TournamentOnsitePayment.Status.EXPECTED
        or tournament.status == TournamentStatus.CANCELLED
        or not user_is_registered(tournament, payment.user)
        or is_player_withdrawn(tournament, payment.user.player)
    ):
        raise RegistrationPaymentError(
            "Получение нельзя подтвердить: ожидание закрыто или участник снят."
        )
    payment.status = TournamentOnsitePayment.Status.RECEIVED
    payment.received_at = timezone.now()
    payment.received_by = received_by
    payment.save(update_fields=["status", "received_at", "received_by"])
    cancel_pending_entry_invoice(tournament, payment.user)
    logger.info(
        "Получен взнос на корте: tournament_id={}, payment_id={}, received_by={}",
        tournament.pk,
        payment.pk,
        received_by.pk,
    )
    return True


@transaction.atomic
def receive_postpayment_on_court(
    tournament: Tournament, invoice_id: int, *, received_by: User
) -> None:
    """Принять сумму выставленного счёта и атомарно закрыть долг постоплаты."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    invoice = (
        tournament.postpayment_invoices.select_for_update()
        .filter(pk=invoice_id)
        .first()
    )
    if not invoice or invoice.status != TournamentPostpaymentInvoice.Status.PENDING:
        raise RegistrationPaymentError(
            "Счёт уже закрыт или недоступен. Проверьте статус в списке."
        )
    if (
        not tournament.allow_onsite_payment
        or tournament.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED)
        or has_received_entry_payment(tournament, invoice.user)
        or tournament.registration_coverages.filter(user_id=invoice.user_id).exists()
        or has_onsite_payment(tournament, invoice.user)
    ):
        raise RegistrationPaymentError(
            "Принять деньги по этому счёту на корте нельзя. Проверьте статус оплаты."
        )
    payment = TournamentOnsitePayment.objects.create(
        tournament=tournament, user=invoice.user, amount=invoice.amount
    )
    confirm_onsite_payment(tournament, payment.pk, received_by=received_by)


@transaction.atomic
def close_expected_onsite_payments(
    tournament: Tournament,
    *,
    reason: Literal[
        "registration_removed", "withdrawn", "tournament_cancelled", "online_payment"
    ],
    user: User | None = None,
) -> int:
    """Закрыть только ожидания; полученные деньги и подтверждения никогда не стирать."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    payments = tournament.onsite_payments.filter(
        status=TournamentOnsitePayment.Status.EXPECTED
    )
    if user is not None:
        payments = payments.filter(user=user)
    status = (
        TournamentOnsitePayment.Status.REPLACED
        if reason == "online_payment"
        else TournamentOnsitePayment.Status.CANCELLED
    )
    return int(
        payments.update(status=status, closed_at=timezone.now(), closed_reason=reason)
    )


@transaction.atomic
def restore_expected_onsite_payments(tournament: Tournament) -> None:
    """При возобновлении восстановить прежний способ и сумму новой записью в истории."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    seen: set[int] = set()
    for payment in (
        tournament.onsite_payments.filter(
            status=TournamentOnsitePayment.Status.CANCELLED,
            closed_reason="tournament_cancelled",
        )
        .select_related("user")
        .order_by("-pk")
    ):
        if payment.user_id in seen:
            continue
        seen.add(payment.user_id)
        if user_is_registered(tournament, payment.user) and not (
            has_onsite_payment(tournament, payment.user)
            or has_received_entry_payment(tournament, payment.user)
            or tournament.registration_coverages.filter(user=payment.user).exists()
        ):
            TournamentOnsitePayment.objects.create(
                tournament=tournament, user=payment.user, amount=payment.amount
            )


@transaction.atomic
def create_onsite_refund_request(
    tournament: Tournament, user: User
) -> TournamentEntryRefundRequest | None:
    """Создать одну заявку на ручной возврат реально полученной суммы на площадке."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    payment = (
        tournament.onsite_payments.select_for_update()
        .filter(user=user, status=TournamentOnsitePayment.Status.RECEIVED)
        .first()
    )
    if payment is None:
        return None
    refund, _ = TournamentEntryRefundRequest.objects.get_or_create(
        onsite_payment=payment,
        defaults={
            "tournament": tournament,
            "user": user,
            "amount": payment.amount,
            "refund_ref": "REF-" + secrets.token_hex(8).upper(),
        },
    )
    return cast(TournamentEntryRefundRequest, refund)


def get_online_entry_payment_amount(
    tournament: Tournament, user: User
) -> Decimal | None:
    """Вернуть реально оплаченный онлайн-взнос, а не изменённую организатором цену."""
    from apps.payments.models import PaymentRecord

    if user.pk not in get_paid_entry_user_ids(tournament):
        return None
    record = (
        PaymentRecord.objects.filter(
            user=user,
            payment_type=PaymentRecord.PaymentType.TOURNAMENT,
            item_id=str(tournament.pk),
            status="succeeded",
        )
        .order_by("-paid_at", "-pk")
        .first()
    )
    if record is not None:
        amount = cast(Decimal, record.amount)
        try:
            total = Decimal(str((record.metadata or {}).get("total_amount", amount)))
        except (ArithmeticError, ValueError):
            total = amount
        if total.is_finite() and total >= amount:
            amount = total
        return amount.quantize(Decimal("0.01"))
    invoice = tournament.postpayment_invoices.filter(
        user=user, status=TournamentPostpaymentInvoice.Status.PAID
    ).first()
    if invoice is not None:
        return cast(Decimal, invoice.amount)
    # В старых регистрациях нет снимка суммы: оставляем прежнее поведение.
    return Decimal(tournament.entry_fee or 0).quantize(Decimal("0.01"))


@transaction.atomic
def create_entry_refund_request(
    tournament: Tournament, user: User
) -> TournamentEntryRefundRequest | None:
    """Запросить ручной возврат полученных денег, не объявляя его выполненным."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    onsite_refund: TournamentEntryRefundRequest | None = create_onsite_refund_request(
        tournament, user
    )
    if onsite_refund is not None:
        return onsite_refund
    amount = get_online_entry_payment_amount(tournament, user)
    if amount is None or amount <= 0:
        return None
    refund = (
        TournamentEntryRefundRequest.objects.filter(
            tournament=tournament,
            user=user,
            onsite_payment__isnull=True,
        )
        .order_by("-pk")
        .first()
    )
    if refund is None:
        refund = TournamentEntryRefundRequest.objects.create(
            tournament=tournament,
            user=user,
            amount=amount,
            refund_ref="REF-" + secrets.token_hex(8).upper(),
        )
    return cast(TournamentEntryRefundRequest, refund)
