"""Сигналы приложения navigation для инвалидации кэша меню."""

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.navigation.context_processors import NAV_MENU_CACHE_KEY
from apps.navigation.models import MenuItem


@receiver(post_save, sender=MenuItem)
@receiver(post_delete, sender=MenuItem)
def invalidate_nav_menu_cache(**kwargs) -> None:
    """Очищает кэш пунктов меню при изменении модели.

    Args:
        **kwargs: Служебные аргументы Django-сигнала.

    Returns:
        None: Ключ кэша удаляется без возврата значения.
    """
    cache.delete(NAV_MENU_CACHE_KEY)
