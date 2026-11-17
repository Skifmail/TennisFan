"""Режим «Старт после набора» для платформенных турниров.

При достижении минимума — уведомление админу (email/Telegram).
При достижении максимума — автоматический запуск и формирование сетки.
"""

from __future__ import annotations

import logging
from typing import Literal

from django.db import transaction
from django.utils import timezone

from apps.tournaments.models import Tournament, TournamentStatus

logger = logging.getLogger(__name__)

ProgressResult = Literal[
    "skipped",
    "min_notified",
    "waiting_max",
    "postpayment_opened",
    "started",
    "start_failed",
]


def _filled_count(tournament: Tournament) -> int:
    """Текущий набор: участники или полные команды.

    Args:
        tournament: Турнир.

    Returns:
        int: Число участников или полных команд.
    """
    if tournament.is_doubles():
        return int(tournament.full_teams_count())
    return int(tournament.participants.count())


def _min_required(tournament: Tournament) -> int | None:
    """Минимум для старта (участники или команды)."""
    if tournament.is_doubles():
        value = tournament.min_teams
    else:
        value = tournament.min_participants
    return int(value) if value is not None else None


def _max_required(tournament: Tournament) -> int | None:
    """Максимум для автостарта (участники или команды)."""
    if tournament.is_doubles():
        value = tournament.max_teams
    else:
        value = tournament.max_participants
    return int(value) if value is not None else None


def _dispatch_generate(tournament: Tournament) -> tuple[bool, str]:
    """Запустить формирование сетки/групп по формату турнира."""
    from .views import _tournament_manual_generate

    ok, message, _ = _tournament_manual_generate(tournament)
    return ok, message


def _notify_min_fill(tournament_id: int) -> None:
    """Отправить уведомление о наборе минимума."""
    try:
        tournament = Tournament.objects.select_related("club").get(pk=tournament_id)
    except Tournament.DoesNotExist:
        return
    from apps.core.telegram_notify import notify_tournament_min_fill_reached

    ok = notify_tournament_min_fill_reached(tournament)
    if not ok:
        logger.warning(
            "Min-fill admin notify failed for tournament pk=%s", tournament_id
        )
    if tournament.club_id:
        try:
            from apps.clubs.notifications import send_tournament_min_fill_to_club

            send_tournament_min_fill_to_club(tournament)
        except Exception:
            logger.exception(
                "Club min-fill notify failed for tournament pk=%s", tournament_id
            )


def _auto_start_tournament(tournament_id: int) -> ProgressResult:
    """Автозапуск турнира после набора максимума."""
    with transaction.atomic():
        locked = Tournament.objects.select_for_update().filter(pk=tournament_id).first()
        if locked is None:
            return "skipped"
        if (
            not locked.start_after_fill
            or locked.bracket_generated
            or locked.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED)
        ):
            return "skipped"

        max_required = _max_required(locked)
        count = _filled_count(locked)
        if max_required is None or count < max_required:
            return "waiting_max"

        if locked.postpayment_window_started_at is not None:
            return "postpayment_opened"
        if locked.allow_postpayment:
            from apps.tournaments.postpayment import (
                get_pending_postpayment_users,
                open_postpayment_window,
            )

            pending_users = get_pending_postpayment_users(locked)
            if pending_users:
                open_postpayment_window(locked)
                locked.refresh_from_db()
                still_pending = get_pending_postpayment_users(locked)
                if still_pending:
                    logger.info(
                        "Opened postpayment window for fill-start %s (%s pending)",
                        locked.slug,
                        len(still_pending),
                    )
                    return "postpayment_opened"
                if locked.postpayment_window_started_at is not None:
                    logger.info(
                        "Postpayment window active for fill-start %s",
                        locked.slug,
                    )
                    return "postpayment_opened"
                # Долги закрыты (например, FanCoin settle) — продолжаем старт.

        ok, msg = _dispatch_generate(locked)
        if ok:
            logger.info("Auto-started fill tournament %s: %s", locked.slug, msg)
            return "started"

        logger.warning("Auto-start failed for fill tournament %s: %s", locked.slug, msg)
        return "start_failed"


def maybe_progress_start_after_fill(tournament: Tournament) -> ProgressResult:
    """Проверить набор и при необходимости уведомить или автозапустить.

    Args:
        tournament: Турнир после подтверждённой регистрации.

    Returns:
        ProgressResult: Краткий код результата для логов/тестов.
    """
    if not getattr(tournament, "start_after_fill", False):
        return "skipped"
    if tournament.bracket_generated:
        return "skipped"
    if tournament.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED):
        return "skipped"
    if tournament.pk is None:
        return "skipped"

    tournament_id = int(tournament.pk)
    notify_min = False
    should_autostart = False
    result: ProgressResult = "skipped"

    with transaction.atomic():
        locked = Tournament.objects.select_for_update().filter(pk=tournament_id).first()
        if locked is None:
            return "skipped"
        if (
            not locked.start_after_fill
            or locked.bracket_generated
            or locked.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED)
        ):
            return "skipped"

        count = _filled_count(locked)
        min_required = _min_required(locked)
        max_required = _max_required(locked)

        if min_required is not None and count < min_required:
            if locked.min_fill_reached_notified_at is not None:
                locked.min_fill_reached_notified_at = None
                locked.save(update_fields=["min_fill_reached_notified_at"])
            return "skipped"

        result = "waiting_max"
        if (
            min_required is not None
            and count >= min_required
            and locked.min_fill_reached_notified_at is None
        ):
            locked.min_fill_reached_notified_at = timezone.now()
            locked.save(update_fields=["min_fill_reached_notified_at"])
            notify_min = True
            result = "min_notified"
            logger.info(
                "Min fill reached for %s: %s/%s, will notify admin",
                locked.slug,
                count,
                min_required,
            )

        if max_required is not None and count >= max_required:
            should_autostart = True

    # Уведомления и генерация — вне select_for_update первого шага.
    if notify_min:
        _notify_min_fill(tournament_id)
    if should_autostart:
        return _auto_start_tournament(tournament_id)

    return result
