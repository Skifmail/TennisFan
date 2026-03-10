"""Вспомогательные функции для подписок и тарифов."""

from decimal import Decimal
from typing import Any, cast

from .models import RegionalTierPrice, UserSubscription


def get_subscription_renew_amount(subscription: UserSubscription) -> Decimal:
    """Рассчитать стоимость продления подписки с учётом города покупки.

    Args:
        subscription: Подписка пользователя.

    Returns:
        Сумма к списанию в рублях.
    """
    tier = subscription.tier
    purchase_city = normalize_city_for_pricing(subscription.purchase_city or "")
    amount: Decimal = cast(Decimal, tier.price)
    if purchase_city and purchase_city != "moscow":
        regional = RegionalTierPrice.objects.filter(tier=tier).first()
        if regional is not None:
            amount = cast(Decimal, regional.price)
    return amount


def normalize_city_for_pricing(city_str: str) -> str:
    """
    Нормализует город для определения тарифа (Москва vs регионы).
    Возвращает "moscow" для Москвы, иначе — приведённую к нижнему регистру строку.
    """
    city = (city_str or "").lower().strip()
    if city in ("moscow", "moskva", "москва"):
        return "moscow"
    return city or "moscow"


def _get_valid_subscription_tier(user: Any) -> Any | None:
    """Вернуть тариф активной подписки пользователя."""
    if not getattr(user, "is_authenticated", False):
        return None

    subscription = getattr(user, "subscription", None)
    if subscription is None or not subscription.is_valid():
        return None

    return getattr(subscription, "tier", None)


def user_can_read_comments(user: Any) -> bool:
    """Проверить доступ пользователя к чтению комментариев.

    Args:
        user (Any): Пользователь Django, для которого проверяется доступ.

    Returns:
        bool: ``True``, если у пользователя есть активная подписка и тариф
        разрешает чтение комментариев.
    """
    tier = _get_valid_subscription_tier(user)
    return bool(tier is not None and getattr(tier, "can_read_comments", False))


def user_can_write_comments(user: Any) -> bool:
    """Проверить доступ пользователя к написанию комментариев.

    Args:
        user (Any): Пользователь Django, для которого проверяется доступ.

    Returns:
        bool: ``True``, если у пользователя есть активная подписка и тариф
        разрешает написание комментариев.
    """
    tier = _get_valid_subscription_tier(user)
    return bool(tier is not None and getattr(tier, "can_write_comments", False))


def user_can_rate_opponents(user: Any) -> bool:
    """Проверить доступ пользователя к оценке соперников.

    Args:
        user (Any): Пользователь Django, для которого проверяется доступ.

    Returns:
        bool: ``True``, если у пользователя есть активная подписка и тариф
        разрешает оценку соперников после матчей.
    """
    tier = _get_valid_subscription_tier(user)
    return bool(tier is not None and getattr(tier, "can_rate_opponents", False))
