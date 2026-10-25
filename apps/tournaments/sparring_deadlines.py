"""Однократное самостоятельное продление спарринговых матчей."""

from datetime import timedelta
from typing import cast

from django.core.exceptions import ValidationError
from django.db import transaction
from loguru import logger

from apps.notifications.services import create_notification
from apps.users.models import Player

from .models import Match, SparringDeadlineExtension
from .utils import get_match_participants


def can_extend_sparring_deadline(match: Match, player: Player | None) -> bool:
    """Проверить право участника добавить неделю к дедлайну."""
    return bool(
        player
        and match.is_sparring()
        and match.deadline
        and match.status in (Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS)
        and player in get_match_participants(match)
        and not SparringDeadlineExtension.objects.filter(
            match=match, player=player
        ).exists()
    )


def _notify_extension(extension_id: int) -> None:
    """Сообщить участникам о новом дедлайне после фиксации транзакции."""
    from django.urls import reverse
    from django.utils import timezone

    extension = SparringDeadlineExtension.objects.select_related("match", "player").get(
        pk=extension_id
    )
    for participant in get_match_participants(extension.match):
        try:
            create_notification(
                user=participant.user,
                message=(
                    f"{extension.player} продлил дедлайн спарринга на неделю: "
                    f"до {timezone.localtime(extension.new_deadline):%d.%m.%Y}."
                ),
                url=reverse("match_detail", kwargs={"pk": extension.match.pk}),
                event_key="match.deadline_changed",
            )
        except Exception:
            logger.exception(
                "Не удалось уведомить игрока {} о продлении {}",
                participant.pk,
                extension.pk,
            )


@transaction.atomic
def extend_sparring_deadline(match_id: int, player: Player | None) -> Match:
    """Добавить семь дней с блокировкой матча и защитой от повторного запроса."""
    match = cast(Match, Match.objects.select_for_update().get(pk=match_id))
    if not can_extend_sparring_deadline(match, player):
        raise ValidationError(
            "Продление недоступно: только участник активного спарринга может продлить дедлайн один раз."
        )
    assert match.deadline is not None
    assert player is not None
    old_deadline = match.deadline
    match.deadline += timedelta(days=7)
    match.deadline_overdue_notified_at = None
    match.save(update_fields=["deadline", "deadline_overdue_notified_at"])
    extension = SparringDeadlineExtension.objects.create(
        match=match,
        player=player,
        old_deadline=old_deadline,
        new_deadline=match.deadline,
    )
    transaction.on_commit(lambda: _notify_extension(extension.pk))
    return match


def prepare_admin_sparring_save(match: Match, changed_fields: set[str]) -> None:
    """Откатить прежний результат перед изменением матча администратором."""
    old = Match.objects.select_for_update().get(pk=match.pk)
    if "deadline" in changed_fields:
        match.deadline_overdue_notified_at = None
    result_fields = {
        "status",
        "winner",
        "player1",
        "player2",
        "partner1",
        "partner2",
        "sport",
        "player1_set1",
        "player2_set1",
        "player1_set2",
        "player2_set2",
        "player1_set3",
        "player2_set3",
        "walkover_no_show_side1",
        "walkover_no_show_side2",
    }
    if not changed_fields.intersection(result_fields):
        return
    if old.rating_status == Match.RatingCalcStatus.CALCULATED:
        from apps.core.sports import Sport
        from apps.users.sport_rating import ensure_sport_profile

        from .signals import _revert_player_rating

        side1 = {old.player1, old.partner1} - {None}
        for player in get_match_participants(old):
            delta = (
                old.rating_delta_player1
                if player in side1
                else old.rating_delta_player2
            )
            _revert_player_rating(player, float(delta or 0), old.sport)
            player.refresh_from_db()
            stats = (
                ensure_sport_profile(player, old.sport)
                if old.sport == Sport.PADEL
                else player
            )
            won = (
                old.winner in side1
                if player in side1
                else old.winner is not None and old.winner not in side1
            )
            stats.matches_played = max(0, stats.matches_played - 1)
            stats.matches_won = max(0, stats.matches_won - int(won))
            stats.save(update_fields=["matches_played", "matches_won"])
    # Следующее сохранение результата должно учесть всех актуальных участников заново.
    Match.objects.filter(pk=old.pk).update(
        status=Match.MatchStatus.SCHEDULED,
        rating_status=Match.RatingCalcStatus.NOT_APPLICABLE,
    )
    match.rating_status = Match.RatingCalcStatus.NOT_APPLICABLE
    match.rating_delta_player1 = 0.0
    match.rating_delta_player2 = 0.0
    if match.status not in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        match.winner = None
        match.winner_team = None
        match.completed_datetime = None
        match.walkover_no_show_side1 = False
        match.walkover_no_show_side2 = False
        for index in (1, 2, 3):
            setattr(match, f"player1_set{index}", None)
            setattr(match, f"player2_set{index}", None)
