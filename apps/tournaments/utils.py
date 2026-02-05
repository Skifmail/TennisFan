"""
Утилиты турниров (участники матча и т.д.).
Используются в views и в telegram_bot без циклических импортов.
"""

from .models import Match


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
    """Пользователи противоположной стороны (без exclude_player). Без bye-игроков."""
    participants = get_match_participants(match)
    participants.discard(exclude_player)
    return [
        p.user for p in participants
        if getattr(p, "user", None) and not getattr(p, "is_bye", False)
    ]
