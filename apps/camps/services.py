"""Атомарные операции набора, подтверждения и ручных расчётов."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from loguru import logger

from apps.notifications.infrastructure.email import send_event_mail as send_mail
from apps.notifications.services import center_enabled, create_notification
from apps.users.models import User

from .communication import invalidate_camp_reminders
from .models import (
    CampApplication,
    CampCancellation,
    CampPackage,
    CampParticipant,
    CampPayment,
    CampSession,
)

ACTIVE_STATUSES = {
    CampApplication.Status.NEW,
    CampApplication.Status.CONTACTED,
    CampApplication.Status.AGREED,
    CampApplication.Status.CONFIRMED,
    CampApplication.Status.WAITLIST,
}
TRANSITIONS: dict[str, set[str]] = {
    "new": {"contacted", "agreed", "confirmed", "waitlist", "cancelled"},
    "contacted": {"agreed", "confirmed", "waitlist", "cancelled"},
    "agreed": {"contacted", "confirmed", "waitlist", "cancelled"},
    "waitlist": {"contacted", "agreed", "confirmed", "cancelled"},
    "confirmed": {"completed", "cancelled"},
    "cancelled": set(),
    "completed": set(),
}


def can_manage(user: User) -> bool:
    """Разрешает операции сотруднику с правом изменения заявок."""
    return bool(
        user.is_active
        and user.is_staff
        and user.has_perm("camps.change_campapplication")
    )


def require_manager(user: User) -> None:
    """Проверяет полномочия также на уровне бизнес-операции."""
    if not can_manage(user):
        raise PermissionDenied


def _notify(application_id: UUID, *, submitted: bool) -> None:
    """Создаёт события ЛК после фиксации данных; доставка управляется центром уведомлений."""
    app = CampApplication.objects.select_related(
        "session__camp", "session__coordinator", "user"
    ).get(pk=application_id)
    event = "camp.application_submitted" if submitted else "camp.application_updated"
    recipients: list[tuple[User, str, str]] = []
    if submitted:
        managers = User.objects.filter(is_active=True, is_superuser=True)
        if app.session.coordinator_id:
            managers = User.objects.filter(
                pk=app.session.coordinator_id, is_active=True
            )
        for manager in managers:
            recipients.append(
                (
                    manager,
                    "Новая заявка на кемп. Откройте список участников заезда.",
                    reverse("camps:manage_application", args=[app.pk]),
                )
            )
    if app.user is not None:
        recipients.append(
            (
                app.user,
                (
                    "Заявка на кемп получена. Место подтвердит координатор."
                    if submitted
                    else f"Заявка на кемп: {app.get_status_display()}."
                ),
                reverse("camps:application", args=[app.pk]),
            )
        )
    if not app.user_id:
        try:
            send_mail(
                (
                    "TennisFan · заявка на кемп"
                    if submitted
                    else "TennisFan · статус участия в кемпе"
                ),
                f"{app.session.camp.title} · {app.session.start_date:%d.%m.%Y}\n"
                + (
                    "Заявка получена. Наличие мест и стоимость подтвердит координатор."
                    if submitted
                    else f"Статус: {app.get_status_display()}."
                )
                + f"\nСвязь с командой: {app.session.contact_email} {app.session.contact_phone}"
                + "\nВернуться к гостевым заявкам: "
                + str(settings.SITE_URL).rstrip("/")
                + reverse("camps:recover"),
                settings.DEFAULT_FROM_EMAIL,
                [app.email],
                event_key=event,
                fail_silently=False,
            )
        except Exception:
            logger.exception(
                "Не удалось отправить гостевое письмо кемпа: заявка {}", application_id
            )
    for recipient, message, url in recipients:
        try:
            create_notification(
                event_key=event, user=recipient, message=message, url=url
            )
            if not center_enabled() and recipient.email:
                from apps.notifications.services import preferences

                if preferences(recipient.pk)[1]:
                    send_mail(
                        "TennisFan · кемпы",
                        message,
                        settings.DEFAULT_FROM_EMAIL,
                        [recipient.email],
                        event_key=event,
                        fail_silently=False,
                    )
        except Exception:
            logger.exception(
                "Не удалось создать уведомление кемпа: заявка {}", application_id
            )


@transaction.atomic
def submit_application(
    *,
    session_id: int,
    submission_key: UUID,
    contact: dict[str, Any],
    participants: list[dict[str, Any]],
    user: User | None,
) -> CampApplication:
    """Сохраняет снимок предложения и состав поездки; повтор не создаёт дубль."""
    session = (
        CampSession.objects.select_for_update()
        .select_related("camp")
        .get(pk=session_id)
    )
    previous = CampApplication.objects.filter(submission_key=submission_key).first()
    if previous:
        if previous.session_id != session.pk or previous.user_id != (
            user.pk if user else None
        ):
            raise PermissionDenied
        return cast(CampApplication, previous)
    if not session.camp.is_published or not session.accepts_applications:
        raise ValidationError("Приём заявок на этот заезд уже закрыт.")
    package = CampPackage.objects.select_for_update().get(pk=contact["package"].pk)
    if package.session_id != session.pk or not package.is_active:
        raise ValidationError("Этот пакет больше недоступен. Выберите другой вариант.")
    if not participants or not any(row["role"] != "companion" for row in participants):
        raise ValidationError("В заявке должен быть хотя бы один игрок.")
    app = CampApplication(
        session=session,
        package=package,
        submission_key=submission_key,
        user=user,
        contact_name=contact["contact_name"],
        email=contact["email"],
        phone=contact["phone"],
        comment=contact.get("comment", ""),
        currency=package.currency,
        offer_snapshot={
            "package": package.name,
            "description": package.description,
            "unit_price": str(package.price) if package.price is not None else None,
            "currency": package.currency,
            "terms": session.terms,
            "included": session.included,
            "excluded": session.excluded,
            "start_date": session.start_date.isoformat(),
            "end_date": session.end_date.isoformat(),
        },
    )
    app.full_clean()
    app.save()
    for row in participants:
        person = CampParticipant(
            application=app,
            **{key: row.get(key) for key in ("name", "role", "age", "level")},
        )
        person.full_clean()
        person.save()
    transaction.on_commit(lambda: _notify(app.pk, submitted=True))
    return cast(CampApplication, app)


@transaction.atomic
def update_application(
    *, application_id: UUID, actor: User, values: dict[str, Any]
) -> CampApplication:
    """Подтверждает места под блокировкой заезда и проверяет переходы статуса."""
    require_manager(actor)
    session_id = CampApplication.objects.values_list("session_id", flat=True).get(
        pk=application_id
    )
    session = CampSession.objects.select_for_update().get(pk=session_id)
    app = CampApplication.objects.select_for_update().get(pk=application_id)
    target = values["status"]
    pending_cancellation = CampCancellation.objects.filter(
        application=app, status="pending"
    )
    if (
        pending_cancellation.exists()
        and target in {"completed", "confirmed"}
        and target != app.status
    ):
        raise ValidationError("Сначала рассмотрите запрос участника на отмену.")
    if target != app.status and target not in TRANSITIONS[app.status]:
        raise ValidationError(
            "Такой переход статуса недоступен. Отменённые и завершённые заявки сохраняются в истории."
        )
    if target == "confirmed" and app.status != "confirmed":
        if (
            session.status not in {"open", "closed"}
            or session.end_date < timezone.localdate()
        ):
            raise ValidationError(
                "Нельзя подтвердить участие в отменённом, завершённом или прошедшем заезде."
            )
        players = app.participants.exclude(role="companion").count()
        if players < 1 or players > session.available_places:
            raise ValidationError(
                "Для всех игроков заявки недостаточно свободных мест. Используйте лист ожидания."
            )
        if values.get("quoted_total") is None:
            raise ValidationError(
                "Перед подтверждением согласуйте общую стоимость заявки."
            )
    if target == "completed" and session.end_date > timezone.localdate():
        raise ValidationError("Поездку можно завершить после окончания заезда.")
    changed = app.status != target
    payment_terms_changed = app.quoted_total != values.get(
        "quoted_total"
    ) or app.payment_due != values.get("payment_due")
    for key in (
        "status",
        "quoted_total",
        "payment_due",
        "accommodation_notes",
        "staff_notes",
    ):
        setattr(app, key, values.get(key))
    app.full_clean()
    app.save()
    if payment_terms_changed:
        invalidate_camp_reminders(app, payment_only=True)
    if changed and target != "new":
        invalidate_camp_reminders(app, team_only=True)
    if target in {"cancelled", "completed"}:
        invalidate_camp_reminders(app)
    if target == "cancelled":
        pending_cancellation.update(
            status="approved",
            resolution="Отмена подтверждена командой. Условия возврата согласуются отдельно.",
            resolved_by=actor,
            resolved_at=timezone.now(),
        )
    if changed:
        transaction.on_commit(lambda: _notify(app.pk, submitted=False))
    return cast(CampApplication, app)


@transaction.atomic
def record_payment(
    *,
    application_id: UUID,
    actor: User,
    amount: Decimal,
    reference: str,
    operation_key: UUID,
) -> CampPayment:
    """Записывает поступление либо возврат без повторной проводки и отрицательного остатка."""
    require_manager(actor)
    app = CampApplication.objects.select_for_update().get(pk=application_id)
    previous = CampPayment.objects.filter(operation_key=operation_key).first()
    if previous:
        if previous.application_id != app.pk:
            raise ValidationError("Операция относится к другой заявке.")
        return cast(CampPayment, previous)
    if not amount or app.paid_amount + amount < 0:
        raise ValidationError(
            "Сумма должна быть ненулевой, а возврат не может превышать внесённую оплату."
        )
    payment = CampPayment(
        application=app,
        amount=amount,
        reference=reference,
        recorded_by=actor,
        operation_key=operation_key,
    )
    payment.full_clean()
    payment.save()
    if app.balance is not None and app.balance <= 0:
        invalidate_camp_reminders(app, payment_only=True)
    return payment
