"""
Управление тренером собственным профилем: публикация, скрытие и удаление.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction

from .models import Coach, Training, TrainingEnrollment

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoachDeletionSummary:
    """Что будет удалено вместе с профилем тренера.

    Attributes:
        trainings: Количество тренировок тренера.
        enrollments: Количество записей игроков на эти тренировки.
    """

    trainings: int
    enrollments: int


def activate_coach(coach: Coach) -> None:
    """Опубликовать профиль тренера в разделе «Наши тренеры».

    Тренировки не включаются автоматически: тренер публикует их сам.

    Args:
        coach: Профиль тренера.
    """
    if coach.is_active:
        return
    coach.is_active = True
    coach.save(update_fields=["is_active"])
    logger.info("Coach profile activated | coach=%s", coach.pk)


def deactivate_coach(coach: Coach) -> int:
    """Скрыть профиль тренера и снять с публикации его тренировки.

    Публичный раздел показывает тренера, пока у него есть активные тренировки,
    поэтому без снятия тренировок профиль остался бы на сайте.

    Args:
        coach: Профиль тренера.

    Returns:
        int: Количество тренировок, снятых с публикации.
    """
    with transaction.atomic():
        hidden = Training.objects.filter(coach=coach, is_active=True).update(
            is_active=False
        )
        if coach.is_active:
            coach.is_active = False
            coach.save(update_fields=["is_active"])
    logger.info(
        "Coach profile deactivated | coach=%s trainings_hidden=%s", coach.pk, hidden
    )
    return int(hidden)


def coach_deletion_summary(coach: Coach) -> CoachDeletionSummary:
    """Посчитать тренировки и записи, которые удалятся вместе с профилем.

    Args:
        coach: Профиль тренера.

    Returns:
        CoachDeletionSummary: Количество тренировок и записей на них.
    """
    return CoachDeletionSummary(
        trainings=Training.objects.filter(coach=coach).count(),
        enrollments=TrainingEnrollment.objects.filter(training__coach=coach).count(),
    )


def delete_coach(coach: Coach) -> CoachDeletionSummary:
    """Удалить профиль тренера вместе с его тренировками и записями на них.

    Аккаунт пользователя и профиль игрока не затрагиваются.

    Args:
        coach: Профиль тренера.

    Returns:
        CoachDeletionSummary: Сколько тренировок и записей было удалено.
    """
    coach_pk = coach.pk
    with transaction.atomic():
        summary = coach_deletion_summary(coach)
        Training.objects.filter(coach=coach).delete()
        coach.delete()
    logger.info(
        "Coach profile deleted | coach=%s trainings=%s enrollments=%s",
        coach_pk,
        summary.trainings,
        summary.enrollments,
    )
    return summary
