"""Регулярное освобождение истёкших временных броней кемпов."""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from loguru import logger

from apps.camps.reservations import expire_reservations


class Command(BaseCommand):
    """Переводит просроченные брони в лист ожидания без работы с оплатой."""

    help = "Освобождение истёкших временных броней кемпов"

    def add_arguments(self, parser: CommandParser) -> None:
        """Добавляет проверку без изменений и отправки уведомлений."""
        parser.add_argument(
            "--dry-run", action="store_true", help="Только посчитать просроченные брони"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Проводит один идемпотентный проход."""
        dry_run = bool(options["dry_run"])
        count = expire_reservations(dry_run=dry_run)
        logger.info(
            "Кемпы: просроченных броней {}, проверка без изменений: {}", count, dry_run
        )
