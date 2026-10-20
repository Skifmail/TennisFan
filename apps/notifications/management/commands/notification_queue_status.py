"""Наблюдение за возрастом очереди уведомлений и зависшими заданиями."""

import json
from datetime import timedelta
from typing import Any, Final

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from loguru import logger

from apps.notifications.models import Delivery

PROCESSING_LEASE: Final = timedelta(minutes=5)
DEFAULT_MAX_AGE_MINUTES: Final = 15


class Command(BaseCommand):
    """Проверяет, что cron разбирает очередь и не оставил зависшие задания.

    Attributes:
        help: Описание команды.
    """

    help = (
        "Показать состояние очереди уведомлений и завершиться ошибкой при отставании."
    )

    def add_arguments(self, parser: Any) -> None:
        """Добавляет пороги проверки и машинный формат вывода.

        Args:
            parser: Парсер аргументов Django.
        """
        parser.add_argument(
            "--max-age-minutes",
            type=int,
            default=DEFAULT_MAX_AGE_MINUTES,
            help="Максимальный возраст просроченного задания в очереди.",
        )
        parser.add_argument(
            "--max-processing-minutes",
            type=int,
            default=10,
            help="Сколько минут задание может оставаться в обработке.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Вывести состояние одной JSON-строкой.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Собирает метрики и возвращает ненулевой код при отставании.

        Args:
            *args: Позиционные аргументы Django.
            **options: Пороги и формат вывода.

        Raises:
            CommandError: Очередь отстаёт или есть зависшие задания.
        """
        now = timezone.now()
        max_age = timedelta(minutes=max(1, int(options["max_age_minutes"])))
        processing_limit = timedelta(
            minutes=max(1, int(options["max_processing_minutes"]))
        )
        queued = Delivery.objects.filter(status="queued")
        due = queued.filter(available_at__lte=now)
        oldest = due.order_by("available_at").first()
        oldest_age = int((now - oldest.available_at).total_seconds()) if oldest else 0
        processing = Delivery.objects.filter(status="processing")
        stuck = processing.filter(available_at__lt=now - processing_limit).count()
        stale_email = Delivery.objects.filter(
            status="processing",
            channel="email",
            available_at__lt=now - PROCESSING_LEASE,
        ).count()
        data = {
            "queued": queued.count(),
            "due": due.count(),
            "scheduled": queued.filter(available_at__gt=now).count(),
            "processing": processing.count(),
            "stuck": stuck,
            "stale_email": stale_email,
            "oldest_due_age_seconds": oldest_age,
            "oldest_due_id": oldest.pk if oldest else None,
        }
        if options["json"]:
            self.stdout.write(json.dumps(data, ensure_ascii=False))
        else:
            self.stdout.write(
                "Очередь уведомлений: "
                f"queued={data['queued']} due={data['due']} "
                f"scheduled={data['scheduled']} processing={data['processing']} "
                f"stuck={stuck} stale_email={stale_email} "
                f"oldest_due_age={oldest_age}s"
            )
        logger.info("Состояние очереди уведомлений", **data)
        if oldest_age > int(max_age.total_seconds()) or stuck or stale_email:
            raise CommandError(
                "Очередь уведомлений отстаёт: "
                f"oldest_due_age={oldest_age}s stuck={stuck} stale_email={stale_email}"
            )
