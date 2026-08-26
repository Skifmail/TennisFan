"""Проверка заполненности профиля и автоматическая верификация игрока.

Модуль намеренно не импортирует модели и другие приложения: он подключается
из `apps.core.decorators`, поэтому импорт моделей на уровне модуля создал бы
циклическую зависимость.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.users.models import Player, User

REQUIRED_PROFILE_FIELDS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "phone",
    "birth_date",
)


def get_missing_profile_fields(user: "User", player: "Player | None") -> list[str]:
    """Вернуть незаполненные обязательные поля профиля.

    Args:
        user: Пользователь, которому принадлежит профиль.
        player: Профиль игрока или None, если он ещё не создан.

    Returns:
        list[str]: Имена незаполненных полей в порядке `REQUIRED_PROFILE_FIELDS`.
    """
    values = {
        "first_name": getattr(user, "first_name", ""),
        "last_name": getattr(user, "last_name", ""),
        "phone": getattr(user, "phone", ""),
        "birth_date": getattr(player, "birth_date", None),
    }
    return [name for name in REQUIRED_PROFILE_FIELDS if not values[name]]


def profile_is_filled(user: "User", player: "Player | None") -> bool:
    """Проверить, что все обязательные поля профиля заполнены.

    Args:
        user: Пользователь, которому принадлежит профиль.
        player: Профиль игрока или None, если он ещё не создан.

    Returns:
        bool: True, если незаполненных обязательных полей нет.
    """
    return not get_missing_profile_fields(user, player)


def try_auto_verify(player: "Player | None") -> bool:
    """Подтвердить игрока, если профиль заполнен и email подтверждён.

    Заменяет ручную модерацию: игрок получает `is_verified` сразу после того,
    как выполнены оба условия. Уже подтверждённый игрок не перезаписывается,
    поэтому функцию безопасно вызывать многократно.

    Args:
        player: Профиль игрока или None.

    Returns:
        bool: True, если флаг был выставлен и сохранён в этом вызове.
    """
    if player is None or player.is_verified:
        return False

    user = player.user
    if not getattr(user, "email_verified", False):
        return False

    if not profile_is_filled(user, player):
        return False

    player.is_verified = True
    player.save(update_fields=["is_verified"])
    return True
