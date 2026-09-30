"""Сигналы users для кэша уведомлений."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.users.context_processors import invalidate_unread_notifications_cache
from apps.users.models import Notification, Player
from apps.users.sport_rating import sync_tennis_profile


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


@receiver(post_save, sender=Player)
def sync_tennis_sport_profile(instance: Player, **kwargs) -> None:
    """Держит теннисный профиль равным полям игрока.

    Args:
        instance: Сохранённый игрок.
        **kwargs: Аргументы сигнала Django.
    """
    sync_tennis_profile(instance)
