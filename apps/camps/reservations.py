"""Временная бронь спортивных мест без предоплаты."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from functools import partial
from typing import cast
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from loguru import logger

from apps.users.models import User

from .communication import invalidate_camp_reminders, send_camp_event
from .models import CampApplication, CampParticipant, CampSession

RESERVABLE_STATUSES = {"new", "contacted", "agreed", "waitlist"}


def reservation_limit(session: CampSession) -> datetime:
    """Ограничивает временную бронь концом календарного дня начала заезда."""
    return cast(
        datetime,
        timezone.make_aware(
            datetime.combine(session.start_date + timedelta(days=1), time.min)
        ),
    )


def default_reservation_deadline(
    session: CampSession, *, current: datetime | None = None
) -> datetime:
    """Сохраняет бронь до кемпа, не сокращая уже действующий срок."""
    limit = reservation_limit(session)
    if current is not None and timezone.is_aware(current) and current > limit:
        return current
    return limit


def available_places_for_application(session: CampSession, application_id: UUID) -> int:
    """Вычитает места других заявок, исключая гонку на границе истечения собственной брони."""
    occupied = (
        CampParticipant.objects.filter(application__session=session)
        .exclude(application_id=application_id)
        .exclude(role="companion")
        .filter(
            Q(application__status="confirmed")
            | Q(
                application__status__in=["new", "contacted", "agreed"],
                application__reservation_expires_at__gt=timezone.now(),
            )
        )
        .count()
    )
    return int(max(0, session.capacity - occupied))


def assign_initial_reservation(app: CampApplication, session: CampSession) -> None:
    """Под блокировкой заезда резервирует всю группу либо ставит заявку в лист ожидания."""
    if not session.auto_reserve:
        return
    players = app.participants.exclude(role="companion").count()
    if players > session.available_places:
        app.status = CampApplication.Status.WAITLIST
    else:
        app.reservation_expires_at = default_reservation_deadline(session)
        app.offer_snapshot["initial_reservation_expires_at"] = (
            app.reservation_expires_at.isoformat()
        )
    app.save(
        update_fields=[
            "status",
            "reservation_expires_at",
            "offer_snapshot",
            "updated_at",
        ]
    )


def _notify_reservation(
    application_id: UUID, *, expected_deadline: datetime, expired: bool
) -> None:
    """Уведомляет только об актуальной версии срока, не требуя оплаты."""
    app = CampApplication.objects.select_related(
        "session__camp", "session__coordinator", "user"
    ).get(pk=application_id)
    if app.reservation_expires_at != expected_deadline:
        return
    if expired and (app.status != "waitlist" or not app.reservation_expired):
        return
    if not expired and not app.has_active_reservation:
        return
    if app.session.status in {"cancelled", "completed"}:
        return
    try:
        if expired:
            send_camp_event(
                app=app,
                event_key="camp.reservation_expired",
                message="Временная бронь кемпа истекла. Места освобождены, заявка в листе ожидания. Свяжитесь с координатором.",
            )
            send_camp_event(
                app=app,
                event_key="camp.reservation_expired",
                message="Временная бронь заявки истекла. Места освобождены. Проверьте заявку и лист ожидания.",
                to_team=True,
            )
        else:
            deadline = timezone.localtime(expected_deadline).strftime(
                "%d.%m.%Y %H:%M %Z"
            )
            send_camp_event(
                app=app,
                event_key="camp.reservation_updated",
                message=f"Места на кемп временно забронированы до {deadline}. Предоплата для брони не нужна.",
            )
    except Exception:
        logger.exception(
            "Не удалось отправить уведомление о временной брони {}", application_id
        )


@transaction.atomic
def reserve_application(
    *, application_id: UUID, actor: User, expires_at: datetime
) -> CampApplication:
    """Выдаёт или продлевает бронь всей группы с повторной проверкой свободных мест."""
    from .services import require_manager

    require_manager(actor)
    session_id = CampApplication.objects.values_list("session_id", flat=True).get(
        pk=application_id
    )
    session = CampSession.objects.select_for_update().get(pk=session_id)
    app = CampApplication.objects.select_for_update().get(pk=application_id)
    if app.status not in RESERVABLE_STATUSES or session.status not in {
        "open",
        "closed",
    }:
        raise ValidationError(
            "Временную бронь можно выдать только для незавершённой заявки и действующего заезда."
        )
    if app.has_pending_cancellation:
        raise ValidationError("Сначала рассмотрите запрос участника на отмену.")
    if (
        timezone.is_naive(expires_at)
        or expires_at <= timezone.now()
        or expires_at > reservation_limit(session)
    ):
        raise ValidationError(
            "Укажите будущий срок брони не позднее конца дня начала заезда."
        )
    own_places = app.participants.exclude(role="companion").count()
    available = available_places_for_application(session, app.pk)
    if not own_places or own_places > available:
        raise ValidationError(
            "Недостаточно мест для всех игроков заявки. Оставьте её в листе ожидания."
        )
    if (
        app.has_active_reservation
        and app.reservation_expires_at is not None
        and expires_at < app.reservation_expires_at
    ):
        raise ValidationError("Продление не может сократить действующий срок брони.")
    if app.reservation_expires_at == expires_at and app.has_active_reservation:
        return cast(CampApplication, app)
    app.reservation_expires_at = expires_at
    if app.status == "waitlist":
        app.status = CampApplication.Status.CONTACTED
    app.full_clean()
    app.save(update_fields=["reservation_expires_at", "status", "updated_at"])
    transaction.on_commit(
        lambda: _notify_reservation(app.pk, expected_deadline=expires_at, expired=False)
    )
    return cast(CampApplication, app)


@transaction.atomic
def _expire_session(session_id: int, now: datetime) -> int:
    """Освобождает просроченные брони одного заезда под той же блокировкой, что и подтверждение."""
    CampSession.objects.select_for_update().get(pk=session_id)
    apps = CampApplication.objects.select_for_update().filter(
        session_id=session_id,
        status__in=["new", "contacted", "agreed"],
        reservation_expires_at__lte=now,
    )
    count = 0
    for app in apps:
        deadline = app.reservation_expires_at
        app.status = CampApplication.Status.WAITLIST
        app.save(update_fields=["status", "updated_at"])
        invalidate_camp_reminders(app)
        invalidate_camp_reminders(app, team_only=True)
        if deadline is not None:
            transaction.on_commit(
                partial(
                    _notify_reservation,
                    app.pk,
                    expected_deadline=deadline,
                    expired=True,
                )
            )
        count += 1
    return count


def expire_reservations(*, dry_run: bool = False) -> int:
    """Переводит истёкшие брони в лист ожидания без отмены заявки и финансовых операций."""
    now = timezone.now()
    expired = CampApplication.objects.filter(
        status__in=["new", "contacted", "agreed"], reservation_expires_at__lte=now
    )
    if dry_run:
        return int(expired.count())
    total = 0
    for session_id in list(
        expired.order_by().values_list("session_id", flat=True).distinct()
    ):
        total += _expire_session(session_id, now)
    return total
