"""Сигналы users для кэша уведомлений."""

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.users.context_processors import UNREAD_NOTIFICATIONS_CACHE_KEY_PREFIX
from apps.users.models import Notification


@receiver(post_save, sender=Notification)
@receiver(post_delete, sender=Notification)
def invalidate_unread_notifications_cache(
    instance: Notification,
    **kwargs,
) -> None:
    """Инвалидирует кэш счётчика непрочитанных уведомлений пользователя.

    Args:
        instance: Изменённое уведомление.
        **kwargs: Дополнительные аргументы Django signal.

    Returns:
        None: Удаляет ключ кэша по user_id.
    """
    cache.delete(f"{UNREAD_NOTIFICATIONS_CACHE_KEY_PREFIX}:{instance.user_id}")
