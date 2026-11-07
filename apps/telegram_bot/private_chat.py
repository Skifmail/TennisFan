"""
Правила доступа к приватному Telegram-чату сообщества.
"""

from __future__ import annotations

from django.utils import timezone

from apps.subscriptions.models import SubscriptionTier, UserSubscription
from apps.users.models import User


def get_private_chat_access_status(
    user: User | None, *, lock: bool = False
) -> tuple[bool, str]:
    """
    Проверить доступ пользователя к приватному чату.

    Возвращает:
        (True, "") если доступ разрешён;
        (False, reason) если доступ запрещён.
    """
    if user is None or user.pk is None:
        return False, "Сначала подключите бота с сайта."
    # Не используем кеш обратной связи user.subscription и не переактивируем тариф.
    # При lock=True вызывающий код держит atomic() до завершения Telegram API.
    subscriptions = UserSubscription.objects.filter(user_id=user.pk)
    if lock:
        current_user = User.objects.select_for_update(no_key=True).get(pk=user.pk)
        if not current_user.is_active:
            return False, "Аккаунт неактивен."
        subscriptions = subscriptions.select_for_update(of=("self",), no_key=True)
    else:
        subscriptions = subscriptions.filter(user__is_active=True).select_related(
            "tier"
        )
    subscription = subscriptions.first()
    if lock and subscription is None:
        # При отсутствии строки подписки её конкурентное создание защищает FK
        # пользователя. После ожидания вставки перечитываем строку ещё раз.
        User.objects.select_for_update().get(pk=user.pk)
        subscription = subscriptions.first()

    if not subscription:
        return False, "Нет активной подписки."
    if not subscription.is_valid() or subscription.start_date > timezone.now():
        return False, "Подписка неактивна или истекла."

    tier = (
        SubscriptionTier.objects.select_for_update(no_key=True).get(
            pk=subscription.tier_id
        )
        if lock
        else subscription.tier
    )
    if not tier.has_private_chat:
        return False, "Ваш тариф не включает доступ в закрытый чат."
    return True, ""


def user_has_private_chat_access(user: User | None, *, lock: bool = False) -> bool:
    """Короткий bool-хелпер для синхронизации доступа."""
    allowed, _ = get_private_chat_access_status(user, lock=lock)
    return allowed
