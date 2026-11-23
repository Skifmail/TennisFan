"""Распределение заявок на тренировки администраторами платформы."""

from __future__ import annotations

from typing import TypeGuard

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from loguru import logger

from apps.users.models import User

from .coach_defaults import coach_defaults_from_user, unique_coach_slug
from .models import Coach, TrainingEnrollment


def is_enrollment_admin(user: AbstractBaseUser | AnonymousUser) -> TypeGuard[User]:
    """Проверить полномочия действующего администратора на распределение заявок."""
    return bool(
        isinstance(user, User)
        and user.is_authenticated
        and user.is_active
        and user.is_staff
        and user.has_perm("training.change_trainingenrollment")
    )


def enrollment_admins() -> list[User]:
    """Вернуть администраторов, которым доступны все записи на тренировки."""
    return [
        user
        for user in User.objects.filter(is_active=True, is_staff=True)
        if is_enrollment_admin(user)
    ]


def available_coaches() -> QuerySet[Coach]:
    """Вернуть активных тренеров для ручного выбора получателя заявки."""
    return Coach.objects.filter(is_active=True).select_related("user").order_by("name")


def notify_enrollment(enrollment_id: int) -> None:
    """Отправить уведомления после фиксации заявки, не прерывая работу администратора."""
    from apps.core.email_service import send_training_enrollment_email

    enrollment = TrainingEnrollment.objects.filter(pk=enrollment_id).first()
    if enrollment is None:
        return
    try:
        send_training_enrollment_email(enrollment)
    except Exception:
        logger.exception("Не удалось уведомить о заявке {}", enrollment_id)


@transaction.atomic
def distribute_enrollment(
    enrollment_id: int,
    *,
    actor: User,
    action: str,
    coach_id: int | None = None,
) -> TrainingEnrollment:
    """Однократно назначить заявку; повторное нажатие не меняет получателя."""
    if not is_enrollment_admin(actor):
        raise PermissionDenied
    enrollment: TrainingEnrollment = TrainingEnrollment.objects.select_for_update().get(
        pk=enrollment_id
    )
    if enrollment.assigned_at is not None:
        raise ValueError("Заявка уже распределена. Обновите страницу.")
    if action == "original":
        coach = Coach.objects.filter(pk=enrollment.requested_coach_id).first()
        if coach is None:
            raise ValueError("Исходный тренер удалён. Выберите другого тренера.")
    elif action == "self":
        # Блокировка пользователя защищает создание профиля при двух одновременных заявках.
        locked_user = User.objects.select_for_update().get(pk=actor.pk)
        coach = Coach.objects.filter(user=locked_user).first()
        if coach is None:
            defaults = coach_defaults_from_user(locked_user)
            defaults["name"] = (defaults["name"] or locked_user.get_display_name())[
                :100
            ]
            coach = Coach.objects.create(
                user=locked_user,
                slug=unique_coach_slug(defaults["name"]),
                is_active=False,
                **defaults,
            )
    elif action == "other":
        coach = available_coaches().filter(pk=coach_id).first()
        if coach is None:
            raise ValueError("Выберите активного тренера.")
    else:
        raise ValueError("Неизвестное действие распределения.")
    enrollment.assigned_coach = coach
    enrollment.assigned_by = actor
    enrollment.assigned_at = timezone.now()
    enrollment.save(
        update_fields=["assigned_coach", "assigned_by", "assigned_at", "updated_at"]
    )
    transaction.on_commit(lambda: notify_enrollment(enrollment.pk))
    logger.info(
        "Заявка {} назначена тренеру {} администратором {}",
        enrollment.pk,
        coach.pk,
        actor.pk,
    )
    return enrollment
