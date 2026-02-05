import logging

from django.apps import AppConfig
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)


def _on_tournament_created(sender, instance, created, **kwargs):
    """При создании турнира — уведомить всех пользователей бота."""
    if created:
        logger.info("Tournament created signal: pk=%s, name=%s", instance.pk, getattr(instance, "name", ""))
        from . import notifications
        notifications.notify_new_tournament(instance)


class TelegramBotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.telegram_bot"
    verbose_name = "Телеграм"

    def ready(self):
        from apps.tournaments.models import Tournament
        post_save.connect(_on_tournament_created, sender=Tournament)
