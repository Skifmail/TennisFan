"""Сигналы users для кэша уведомлений."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.users.context_processors import invalidate_unread_notifications_cache
from apps.users.models import Notification


@receiver(post_save, sender=Notification)
@receiver(post_delete, sender=Notification)
def clear_unread_notifications_cache_on_change(
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
    invalidate_unread_notifications_cache(instance.user_id)
