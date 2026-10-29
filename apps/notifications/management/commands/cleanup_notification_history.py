"""Ретенция журнала доставки, подтверждений и неактивных устройств."""

from datetime import timedelta
from typing import Any, Final

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from loguru import logger

from apps.notifications.device_binding import revoke_devices
from apps.notifications.models import (
    Delivery,
    PushCodeLimit,
    PushConfirmation,
    PushDevice,
)

DEFAULT_DELIVERY_DAYS: Final = 30
DEFAULT_DEVICE_DAYS: Final = 30
DEFAULT_CONFIRMATION_DAYS: Final = 1
FINISHED_STATUSES: Final = ("accepted", "skipped", "failed", "uncertain")


class Command(BaseCommand):
    """Удаляет только завершённые технические записи, не историю ЛК.

    Attributes:
        help: Описание команды.
    """

    help = "Удалить устаревшие задания доставки, коды и неактивные push-устройства."

    def add_arguments(self, parser: Any) -> None:
        """Добавляет сроки хранения и режим предпросмотра.

        Args:
            parser: Парсер аргументов Django.
        """
        parser.add_argument("--delivery-days", type=int, default=DEFAULT_DELIVERY_DAYS)
        parser.add_argument("--device-days", type=int, default=DEFAULT_DEVICE_DAYS)
        parser.add_argument(
            "--confirmation-days", type=int, default=DEFAULT_CONFIRMATION_DAYS
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        """Считает и при необходимости удаляет устаревшие записи.

        Args:
            *args: Позиционные аргументы Django.
            **options: Сроки хранения и режим предпросмотра.
        """
        now = timezone.now()
        delivery_cutoff = now - timedelta(days=max(1, int(options["delivery_days"])))
        device_cutoff = now - timedelta(days=max(1, int(options["device_days"])))
        confirmation_cutoff = now - timedelta(
            days=max(1, int(options["confirmation_days"]))
        )
        deliveries = Delivery.objects.filter(
            status__in=FINISHED_STATUSES, finished_at__lt=delivery_cutoff
        )
        confirmations = PushConfirmation.objects.filter(
            expires_at__lt=confirmation_cutoff
        )
        devices = PushDevice.objects.filter(
            Q(expires_at__lt=device_cutoff)
            | Q(active=False, updated_at__lt=device_cutoff)
        )
        limits = PushCodeLimit.objects.filter(resets_at__lt=delivery_cutoff)
        counts = {
            "deliveries": deliveries.count(),
            "confirmations": confirmations.count(),
            "devices": devices.count(),
            "code_limits": limits.count(),
        }
        if options["dry_run"]:
            self.stdout.write(f"К удалению: {counts}")
            return
        # Удаление записи без удаления подписки оставило бы живой адрес у провайдера.
        counts["revoked"] = revoke_devices(list(devices))
        devices.delete()
        confirmations.delete()
        deliveries.delete()
        limits.delete()
        self.stdout.write(f"Удалено: {counts}")
        logger.info("Ретенция уведомлений выполнена", **counts)
