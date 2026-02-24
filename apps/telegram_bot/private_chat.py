"""
Правила доступа к приватному Telegram-чату сообщества.
"""

from __future__ import annotations

from typing import Any


def get_private_chat_access_status(user: Any) -> tuple[bool, str]:
    """
    Проверить доступ пользователя к приватному чату.

    Возвращает:
        (True, "") если доступ разрешён;
        (False, reason) если доступ запрещён.
    """
    try:
        subscription = getattr(user, "subscription", None)
    except Exception:
        subscription = None

    if not subscription:
        return False, "Нет активной подписки."
    if not subscription.is_valid():
        return False, "Подписка неактивна или истекла."

    tier = subscription.tier
    if not tier.has_private_chat:
        return False, "Ваш тариф не включает доступ в закрытый чат."
    return True, ""


def user_has_private_chat_access(user: Any) -> bool:
    """Короткий bool-хелпер для синхронизации доступа."""
    allowed, _ = get_private_chat_access_status(user)
    return allowed
