"""
Удаление solo-команд (игроков без партнёра) из парных турниров при формировании сетки.
"""

import logging

from django.urls import reverse

from apps.users.models import Notification

from .models import Tournament, TournamentTeam

logger = logging.getLogger(__name__)


def remove_solo_teams_from_doubles_tournament(tournament: Tournament) -> int:
    """
    Удалить из парного турнира команды без партнёра (player2=null).
    Игроки не участвуют в турнире — им отправляется уведомление,
    восстанавливается лимит регистраций (+1).
    Возвращает количество удалённых команд.
    """
    if not tournament.is_doubles():
        return 0

    solo_teams = list(
        tournament.teams.filter(player2__isnull=True).select_related("player1__user")
    )
    if not solo_teams:
        return 0

    removed = 0
    url = reverse("tournament_detail", args=[tournament.slug])

    for team in solo_teams:
        player = team.player1
        try:
            sub = player.user.subscription
            if sub and sub.is_valid() and not sub.tier.is_unlimited:
                sub.decrement_usage()
        except Exception:
            pass

        Notification.objects.create(
            user=player.user,
            message=(
                f"Вы были удалены из турнира «{tournament.name}»: "
                "не удалось найти партнёра до дедлайна регистрации. "
                "Лимит регистраций восстановлен (+1)."
            ),
            url=url,
        )

        team.delete()
        removed += 1
        logger.info(
            "Removed solo team from doubles tournament %s: player %s",
            tournament.slug,
            player,
        )

    return removed
