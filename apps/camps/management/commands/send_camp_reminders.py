"""Плановая отправка напоминаний собственных кемпов."""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from loguru import logger

from apps.camps.reminders import send_camp_reminders


class Command(BaseCommand):
    """Запускается существующим cron раз в час, не дублируя один повод."""

    help = "Напоминания о поездке, доплате и заявках кемпов без ответа"

    def add_arguments(self, parser: CommandParser) -> None:
        """Добавляет проверку без сохранения и отправки."""
        parser.add_argument(
            "--dry-run", action="store_true", help="Только посчитать подходящие поводы"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Выполняет один проход и пишет итог в журнал."""
        dry_run = bool(options["dry_run"])
        count = send_camp_reminders(dry_run=dry_run)
        logger.info("Кемпы: {} напоминаний, проверка без отправки: {}", count, dry_run)
