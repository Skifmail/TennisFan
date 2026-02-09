"""
Утилиты турниров (участники матча и т.д.).
Используются в views и в telegram_bot без циклических импортов.
"""

from .models import Match, Tournament


def get_tournament_participant_users(tournament: Tournament) -> list:
    """
    Список пользователей (User) — всех участников турнира (для рассылки уведомлений).
    Одиночный: participants. Парный: игроки из всех команд (player1, player2).
    """
    users = set()
    if tournament.is_doubles():
        for team in tournament.teams.select_related("player1__user", "player2__user"):
            if team.player1_id and getattr(team.player1, "user_id", None) and not getattr(team.player1, "is_bye", False):
                users.add(team.player1.user)
            if team.player2_id and getattr(team.player2, "user_id", None) and not getattr(team.player2, "is_bye", False):
                users.add(team.player2.user)
    else:
        for player in tournament.participants.select_related("user"):
            if getattr(player, "user_id", None) and not getattr(player, "is_bye", False):
                users.add(player.user)
    return list(users)


def get_match_participants(match: Match) -> set:
    """
    Возвращает множество игроков (Player) — участников матча.
    Одиночные: player1, player2. Парные: все из team1 и team2.
    """
    participants = set()
    if match.team1_id and match.team2_id:
        for team in (match.team1, match.team2):
            if team:
                if team.player1_id:
                    participants.add(team.player1)
                if team.player2_id:
                    participants.add(team.player2)
    else:
        if match.player1_id:
            participants.add(match.player1)
        if match.player2_id:
            participants.add(match.player2)
    return participants


def get_match_participant_users(match: Match) -> list:
    """Список пользователей (User) — участников матча (для уведомлений). Без bye-игроков."""
    participants = get_match_participants(match)
    return [
        p.user for p in participants
        if getattr(p, "user_id", None) and not getattr(p, "is_bye", False)
    ]


def get_match_opponent_users(match: Match, exclude_player) -> list:
    """
    Пользователи только противоположной стороны (соперники).
    В парном матче возвращаются только игроки другой команды; сокомандник не входит.
    Без bye-игроков.
    """
    if match.team1_id and match.team2_id:
        if not match.team1 or not match.team2:
            return []
        in_team1 = exclude_player in (match.team1.player1, match.team1.player2)
        in_team2 = exclude_player in (match.team2.player1, match.team2.player2)
        if in_team1:
            opponent_team = match.team2
        elif in_team2:
            opponent_team = match.team1
        else:
            return []
        return [
            p.user
            for p in (opponent_team.player1, opponent_team.player2)
            if p and getattr(p, "user_id", None) and not getattr(p, "is_bye", False)
        ]
    # Одиночный матч
    other = None
    if match.player1_id and exclude_player != match.player1:
        other = match.player1
    elif match.player2_id and exclude_player != match.player2:
        other = match.player2
    if not other or getattr(other, "is_bye", False):
        return []
    if getattr(other, "user_id", None):
        return [other.user]
    return []
