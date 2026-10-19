"""Ограниченный запуск очереди из существующего cron-контейнера."""

from typing import Any

from django.core.management.base import BaseCommand
from loguru import logger

from apps.notifications.worker import process_deliveries


class Command(BaseCommand):
    """Обрабатывает до ста доставок за запуск без массового HTTP-запроса.

    Attributes:
        help: Описание команды.
    """

    help = "Обработать очередь уведомлений ограниченным пакетом."

    def handle(self, *args: Any, **options: Any) -> None:
        """Запускает worker с конечным числом заданий.

        Args:
            *args: Позиционные аргументы Django.
            **options: Служебные параметры команды.
        """
        logger.info("Очередь уведомлений обработана", processed=process_deliveries())
