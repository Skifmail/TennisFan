"""
Назначение бесплатного тарифа и возврат на него после окончания платного.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from django.utils import timezone

from apps.subscriptions.models import SubscriptionTier, UserSubscription

logger = logging.getLogger(__name__)

FREE_TIER_NAME = "free"


def get_free_tier() -> SubscriptionTier | None:
    """Вернуть системный тариф Free.

    Returns:
        SubscriptionTier | None: Тариф с кодом ``free`` либо ``None``, если его нет.
    """
    return cast(
        SubscriptionTier | None,
        SubscriptionTier.objects.filter(name=FREE_TIER_NAME).first(),
    )


def ensure_current_subscription(user: Any) -> UserSubscription | None:
    """Выдать бессрочный Free или вернуть на него после окончания платного тарифа.

    Платный тариф не трогается, пока его ``end_date`` в будущем. Баланс FAN-token
    при возврате на Free сохраняется. Город покупки сбрасывается: запрет смены
    населённого пункта на Москву действует только у платного регионального тарифа.

    Args:
        user: Пользователь Django. Аноним и пользователь без pk пропускаются.

    Returns:
        UserSubscription | None: Текущая подписка либо ``None``, если тарифа Free нет.
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return None

    free = get_free_tier()
    if free is None:
        try:
            return cast(UserSubscription, user.subscription)
        except UserSubscription.DoesNotExist:
            return None

    subscription: UserSubscription
    try:
        subscription = cast(UserSubscription, user.subscription)
    except UserSubscription.DoesNotExist:
        subscription = UserSubscription(
            user=user,
            tier=free,
            is_active=True,
            start_date=timezone.now(),
        )
        subscription.save()
        logger.info("Free subscription assigned | user=%s", user.pk)
        return subscription

    if subscription.tier_id == free.pk:
        if (
            subscription.end_date is not None
            or not subscription.is_active
            or subscription.cancelled_at is not None
            or subscription.purchase_city
        ):
            subscription.tier = free
            subscription.is_active = True
            subscription.cancelled_at = None
            subscription.end_date = None
            subscription.purchase_city = ""
            subscription.save()
        return cast(UserSubscription, subscription)

    if subscription.end_date is not None and subscription.end_date <= timezone.now():
        logger.info(
            "Paid subscription ended, reverting to free | user=%s tier=%s",
            user.pk,
            subscription.tier_id,
        )
        subscription.tier = free
        subscription.is_active = True
        subscription.cancelled_at = None
        subscription.end_date = None
        subscription.purchase_city = ""
        subscription.start_date = timezone.now()
        subscription.save()
    return cast(UserSubscription, subscription)
