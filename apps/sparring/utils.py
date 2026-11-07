"""Sparring app utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.users.models import User


def user_has_sparring_access(user: User) -> bool:
    """
    Вернуть True, если текущий тариф пользователя разрешает спарринги.

    Учитывается действующая подписка, включая бессрочный Free.
    Галочка ``has_sparring`` читается с тарифа, который сейчас назначен.
    """
    from apps.subscriptions.services import ensure_current_subscription

    subscription = ensure_current_subscription(user)
    if subscription is None or not subscription.is_valid():
        return False
    return bool(subscription.tier.has_sparring)
