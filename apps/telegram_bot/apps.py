from typing import Any

from django.apps import AppConfig
from django.db.models.signals import post_save
from loguru import logger


def _on_tournament_created(
    sender: Any, instance: Any, created: bool, **kwargs: Any
) -> None:
    """Сохранить подготовку вместе с новым турниром; сеть работает только в cron."""
    if created and not kwargs.get("raw"):
        _notify_after_commit(instance.pk)
        logger.info("Сохранено задание уведомления о турнире: pk={}", instance.pk)


def _notify_after_commit(tournament_pk: int) -> None:
    """Совместимый вход для сохраняемой подготовки, без отправки в сеть."""
    from . import notifications

    notifications.notify_new_tournament_by_pk(tournament_pk)


class TelegramBotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.telegram_bot"
    verbose_name = "Телеграм"

    def ready(self) -> None:
        """Подключить создание задания уведомления без фоновых потоков."""
        from apps.tournaments.models import Tournament

        post_save.connect(
            _on_tournament_created,
            sender=Tournament,
            dispatch_uid="telegram_tournament_announcement",
        )
