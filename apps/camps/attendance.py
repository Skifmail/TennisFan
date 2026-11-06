"""Подтверждение намерения приехать без оплаты и без изменения решения координатора."""

from __future__ import annotations

from collections.abc import Collection
from typing import cast
from uuid import UUID

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from loguru import logger

from apps.users.models import User

from .communication import invalidate_camp_reminders, send_camp_event
from .models import CampApplication, CampSession

SALT = "camps.attendance.v1"
TOKEN_MAX_AGE = 8 * 24 * 60 * 60


def _identity(app: CampApplication) -> str:
    """Привязывает ссылку к адресу, не помещая email в открытый токен."""
    return str(salted_hmac(SALT, app.email.strip().lower()).hexdigest())


def attendance_token(app: CampApplication) -> str:
    """Выдаёт подписанную ссылку только для гостя на текущую дату начала."""
    if app.user_id is not None:
        raise ValidationError("Участник аккаунта подтверждает поездку в своей заявке.")
    return str(
        signing.dumps(
            {
                "application": str(app.pk),
                "start": app.session.start_date.isoformat(),
                "identity": _identity(app),
            },
            salt=SALT,
            compress=True,
        )
    )


def guest_attendance_application(token: str) -> CampApplication | None:
    """Проверяет подпись и срок, не выдавая доступ к данным заявки или аккаунту."""
    try:
        data = signing.loads(token, salt=SALT, max_age=TOKEN_MAX_AGE)
        if not isinstance(data, dict):
            return None
        app = CampApplication.objects.select_related("session__camp").get(
            pk=data.get("application"), user__isnull=True
        )
        if data.get(
            "start"
        ) != app.session.start_date.isoformat() or not constant_time_compare(
            str(data.get("identity", "")), _identity(app)
        ):
            return None
        return cast(CampApplication, app) if app.can_confirm_attendance else None
    except (
        signing.BadSignature,
        CampApplication.DoesNotExist,
        ValidationError,
        ValueError,
        TypeError,
    ):
        return None


def _notify_team(pk: UUID) -> None:
    """Сообщает команде о подтверждении, не меняя расчёты и статус участия."""
    app = CampApplication.objects.select_related(
        "session__camp", "session__coordinator", "user"
    ).get(pk=pk)
    if not app.attendance_confirmed or not app.can_confirm_attendance:
        return
    try:
        send_camp_event(
            app=app,
            event_key="camp.attendance_confirmed",
            message="Участник подтвердил поездку на кемп. Ответ сохранён в заявке.",
            to_team=True,
        )
    except Exception:
        logger.exception("Не удалось уведомить команду о подтверждении поездки {}", pk)


@transaction.atomic
def confirm_attendance(
    *,
    application_id: UUID,
    actor: User | None = None,
    guest_ids: Collection[str] = (),
    token: str | None = None,
) -> CampApplication:
    """Записывает ответ владельца для текущей даты; повторный POST не создаёт новых эффектов."""
    session_id = CampApplication.objects.values_list("session_id", flat=True).get(
        pk=application_id
    )
    CampSession.objects.select_for_update().get(pk=session_id)
    app = (
        CampApplication.objects.select_for_update()
        .select_related("session")
        .get(pk=application_id)
    )
    linked = guest_attendance_application(token) if token else None
    if not (
        (actor is not None and app.user_id == actor.pk)
        or str(app.pk) in guest_ids
        or (linked is not None and linked.pk == app.pk)
    ):
        raise PermissionDenied
    if not app.can_confirm_attendance:
        raise ValidationError(
            "Подтверждение сейчас недоступно. Свяжитесь с координатором: возможно, бронь отменена или вы в листе ожидания."
        )
    if app.attendance_confirmed:
        return cast(CampApplication, app)
    app.attendance_confirmed_at = timezone.now()
    app.attendance_start_date = app.session.start_date
    app.save(
        update_fields=["attendance_confirmed_at", "attendance_start_date", "updated_at"]
    )
    invalidate_camp_reminders(app, departure_only=True)
    transaction.on_commit(lambda: _notify_team(app.pk))
    return cast(CampApplication, app)
