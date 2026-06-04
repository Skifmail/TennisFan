"""Хелперы для завершения матчей в тестах."""

from __future__ import annotations

from django.utils import timezone

from apps.tournaments.models import Match, Tournament
from apps.users.models import Player


def make_completed_sparring_match(player1: Player, player2: Player) -> Match:
    """Создать завершённый одиночный спарринг между двумя игроками.

    Args:
        player1: Первый участник.
        player2: Второй участник.

    Returns:
        Созданный матч со статусом COMPLETED.
    """
    return Match.objects.create(
        match_type=Match.MatchType.SPARRING,
        player1=player1,
        player2=player2,
        status=Match.MatchStatus.COMPLETED,
        completed_datetime=timezone.now(),
    )


def complete_match(match: Match) -> None:
    """Завершить матч: выбрать победителя и сохранить статус COMPLETED.

    Args:
        match: Матч для завершения.

    Returns:
        None
    """
    if getattr(match.player2, "is_bye", False):
        winner = match.player1
    elif getattr(match.player1, "is_bye", False):
        winner = match.player2
    else:
        winner = (
            match.player1
            if match.player1.total_points >= match.player2.total_points
            else match.player2
        )
    match.winner = winner
    match.status = Match.MatchStatus.COMPLETED
    match.save()


def complete_tvd_group_stage(tournament: Tournament) -> None:
    """Завершить все групповые матчи TVD-турнира и пересчитать места.

    Args:
        tournament: Турнир в формате TVD.

    Returns:
        None
    """
    from apps.tournaments.tvd import recalculate_group_standings

    for match in tournament.matches.filter(tvd_stage="group"):
        match.winner = match.player1
        match.status = Match.MatchStatus.COMPLETED
        match.player1_set1 = 6
        match.player2_set1 = 4
        match.save()
    for group in tournament.tvd_groups.all():
        recalculate_group_standings(group)
