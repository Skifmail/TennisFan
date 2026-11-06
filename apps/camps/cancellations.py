"""Запрос отмены и решение координатора без автоматических денежных операций."""

from __future__ import annotations

from collections.abc import Collection
from typing import cast
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from loguru import logger

from apps.users.models import User

from .communication import invalidate_camp_reminders, send_camp_event
from .models import CampApplication, CampCancellation, CampSession
from .services import ACTIVE_STATUSES, require_manager


def _notify_cancellation(pk: int, *, requested: bool) -> None:
    """Уведомляет после фиксации запроса или решения, сохраняя ошибки доставки в журнале."""
    request = CampCancellation.objects.select_related(
        "application__session__camp",
        "application__session__coordinator",
        "application__user",
    ).get(pk=pk)
    app = request.application
    try:
        if requested:
            send_camp_event(
                app=app,
                event_key="camp.cancellation_requested",
                message="Участник запросил отмену кемпа. Откройте заявку и согласуйте решение и возврат.",
                to_team=True,
            )
            send_camp_event(
                app=app,
                event_key="camp.cancellation_requested",
                message="Запрос на отмену получен. Ответ команды — в заявке. Временная бронь действует до своего срока, возврат согласуем отдельно.",
            )
        else:
            send_camp_event(
                app=app,
                event_key="camp.cancellation_updated",
                message=f"{request.get_status_display()}. Ответ команды — в заявке. Возврат согласуется отдельно.",
            )
    except Exception:
        logger.exception("Ошибка уведомления о запросе отмены {}", pk)


@transaction.atomic
def request_cancellation(
    *,
    application_id: UUID,
    actor: User | None,
    guest_application_ids: Collection[str],
    submission_key: UUID,
    reason: str,
) -> CampCancellation:
    """Принимает запрос владельца, сохраняя участие и спортивное место."""
    app = CampApplication.objects.select_for_update().get(pk=application_id)
    if not (
        (actor is not None and app.user_id == actor.pk)
        or str(app.pk) in guest_application_ids
    ):
        raise PermissionDenied
    previous = CampCancellation.objects.filter(submission_key=submission_key).first()
    if previous:
        if previous.application_id != app.pk:
            raise PermissionDenied
        return cast(CampCancellation, previous)
    pending = CampCancellation.objects.filter(application=app, status="pending").first()
    if pending:
        return cast(CampCancellation, pending)
    if app.status not in ACTIVE_STATUSES or app.session.end_date < timezone.localdate():
        raise ValidationError(
            "Для этой заявки запрос на отмену уже недоступен. Свяжитесь с координатором."
        )
    request = CampCancellation(
        application=app, submission_key=submission_key, reason=reason.strip()
    )
    request.full_clean()
    request.save()
    invalidate_camp_reminders(app)
    transaction.on_commit(lambda: _notify_cancellation(request.pk, requested=True))
    return request


@transaction.atomic
def decide_cancellation(
    *,
    request_id: int,
    application_id: UUID,
    actor: User,
    decision: str,
    resolution: str,
) -> CampCancellation:
    """Закрывает запрос и при одобрении освобождает места, не создавая возвратов."""
    require_manager(actor)
    session_id = CampApplication.objects.values_list("session_id", flat=True).get(
        pk=application_id
    )
    CampSession.objects.select_for_update().get(pk=session_id)
    app = CampApplication.objects.select_for_update().get(pk=application_id)
    request = CampCancellation.objects.select_for_update().get(
        pk=request_id, application=app
    )
    if request.status != "pending":
        return cast(CampCancellation, request)
    if decision not in {"approved", "rejected"} or not resolution.strip():
        raise ValidationError("Выберите решение и напишите ответ участнику.")
    if app.status not in ACTIVE_STATUSES:
        raise ValidationError(
            "Заявка уже закрыта. Обновите страницу и проверьте историю."
        )
    if decision == "approved":
        app.status = CampApplication.Status.CANCELLED
        app.reservation_expires_at = None
        app.save(update_fields=["status", "reservation_expires_at", "updated_at"])
        invalidate_camp_reminders(app)
        invalidate_camp_reminders(app, team_only=True)
    request.status = decision
    request.resolution = resolution.strip()
    request.resolved_by = actor
    request.resolved_at = timezone.now()
    request.save(update_fields=["status", "resolution", "resolved_by", "resolved_at"])
    transaction.on_commit(lambda: _notify_cancellation(request.pk, requested=False))
    return cast(CampCancellation, request)
