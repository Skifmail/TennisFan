"""Сигналы core для инвалидации кэшированных данных шаблонов."""

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.core.context_processors import FOOTER_SOCIAL_LINKS_CACHE_KEY
from apps.core.models import FooterSocialLink


@receiver(post_save, sender=FooterSocialLink)
@receiver(post_delete, sender=FooterSocialLink)
def invalidate_footer_social_links_cache(**kwargs) -> None:
    """Сбрасывает кэш ссылок соцсетей в футере.

    Args:
        **kwargs: Служебные параметры сигнала Django.

    Returns:
        None: Кэш обновляется через удаление ключа.
    """
    cache.delete(FOOTER_SOCIAL_LINKS_CACHE_KEY)
