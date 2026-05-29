"""Единообразное отображение имён пользователей и игроков."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.users.models import Player, User


def format_user_display_name(user: User | None) -> str:
    """Форматирует имя пользователя для UI: «Имя Фамилия».

    Args:
        user: Пользователь платформы.

    Returns:
        str: Отображаемое имя или email, если имя не заполнено.
    """
    if user is None:
        return ""
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or str(user.email or "")


def format_player_display_name(player: Player | None) -> str:
    """Форматирует имя игрока для UI.

    Args:
        player: Профиль игрока.

    Returns:
        str: «Свободный круг» для bye или имя пользователя в формате «Имя Фамилия».
    """
    if player is None:
        return ""
    if getattr(player, "is_bye", False):
        return "Свободный круг"
    return format_user_display_name(player.user)
