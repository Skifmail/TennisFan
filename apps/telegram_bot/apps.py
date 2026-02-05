import logging

from django.apps import AppConfig
from django.db import transaction
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)


def _on_tournament_created(sender, instance, created, **kwargs):
    """При создании турнира — уведомить всех пользователей бота после коммита транзакции."""
    if created:
        pk = instance.pk
        logger.info("Tournament created signal: pk=%s, name=%s", pk, getattr(instance, "name", ""))
        # Запускаем уведомление после commit, иначе в фоновом потоке турнир ещё не виден (транзакция не закоммичена).
        transaction.on_commit(lambda: _notify_after_commit(pk))


def _notify_after_commit(tournament_pk: int) -> None:
    from . import notifications
    notifications.notify_new_tournament_by_pk(tournament_pk)


class TelegramBotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.telegram_bot"
    verbose_name = "Телеграм"

    def ready(self):
        from apps.tournaments.models import Tournament
        post_save.connect(_on_tournament_created, sender=Tournament)
