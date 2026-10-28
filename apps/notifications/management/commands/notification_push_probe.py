"""Пробный push через боевую очередь: отличает сбой конвейера от сбоя устройства."""

import time
from datetime import timedelta
from typing import Any, Final

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from django.utils import timezone
from loguru import logger

from apps.notifications.domain.catalog import EVENTS
from apps.notifications.domain.rules import BODY_LIMIT, TITLE_LIMIT
from apps.notifications.infrastructure.push import fetch_delivery_stats
from apps.notifications.models import Delivery, PushDevice
from apps.notifications.services import center_enabled, event_policy, preferences

PROBE_LIFETIME: Final = timedelta(hours=1)
POLL_SECONDS: Final = 5
DEFAULT_WAIT_SECONDS: Final = 90
PROBE_TITLE: Final = "Проверка уведомлений TennisFan"
PROBE_BODY: Final = "Пробное уведомление через общую очередь доставки."
DEFAULT_EVENT: Final = "match.created"


class Command(BaseCommand):
    """Ставит тестовое задание в очередь и показывает, что сделал worker.

    Тестовая кнопка в настройках отправляет push напрямую из web-процесса,
    поэтому она не проверяет очередь, cron-контейнер и повторные проверки
    согласий. Эта команда проходит тот же путь, что и рабочие события.

    Attributes:
        help: Описание команды.
    """

    help = (
        "Поставить пробный push в общую очередь доставки и дождаться решения worker. "
        "Показывает подписки устройства, статус задания и статистику OneSignal."
    )

    def add_arguments(self, parser: Any) -> None:
        """Добавляет адресата, текст и время ожидания.

        Args:
            parser: Парсер аргументов Django.
        """
        parser.add_argument(
            "--email",
            required=True,
            help="Email получателя с подтверждённым устройством.",
        )
        parser.add_argument(
            "--event",
            default=DEFAULT_EVENT,
            help="Событие реестра, чья политика проверяется worker.",
        )
        parser.add_argument("--title", default=PROBE_TITLE, help="Заголовок push.")
        parser.add_argument("--body", default=PROBE_BODY, help="Текст push.")
        parser.add_argument(
            "--wait",
            type=int,
            default=DEFAULT_WAIT_SECONDS,
            help="Сколько секунд ждать worker; 0 — только поставить в очередь.",
        )
        parser.add_argument(
            "--check-provider",
            action="store_true",
            help="Запросить у OneSignal счётчики доставки принятого сообщения.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Проверяет предусловия, ставит задание и печатает результат.

        Args:
            *args: Позиционные аргументы Django.
            **options: Email, событие, текст, ожидание и флаг статистики.

        Raises:
            CommandError: Центр выключен, адресат или предусловия не подходят,
                либо worker не принял задание за отведённое время.
        """
        if not center_enabled():
            raise CommandError("Центр уведомлений выключен: очередь не обрабатывается.")
        email = str(options["email"]).strip()
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError("Пользователь с таким email не найден.")
        event_key = str(options["event"])
        if event_key not in EVENTS:
            raise CommandError("Событие отсутствует в реестре уведомлений.")
        if not preferences(user.pk)[0]:
            raise CommandError(
                "У пользователя выключены push-уведомления в настройках: "
                "worker пропустит задание без обращения к провайдеру."
            )
        if not event_policy(event_key).push_enabled:
            raise CommandError(
                f"Push для события {event_key} выключен администратором: "
                "worker пропустит задание без обращения к провайдеру."
            )
        devices = list(
            PushDevice.objects.filter(
                user=user,
                active=True,
                expires_at__gt=timezone.now(),
                subscription_id__isnull=False,
            ).values_list("subscription_id", flat=True)
        )
        if not devices:
            raise CommandError(
                "Нет активных подтверждённых устройств: включите push в браузере "
                "и подтвердите устройство кодом."
            )
        self.stdout.write(
            f"Подписки устройства: {', '.join(str(item) for item in devices)}"
        )
        now = timezone.now()
        item = Delivery.objects.create(
            event_key=event_key,
            user=user,
            channel="push",
            title=str(options["title"])[:TITLE_LIMIT],
            body=str(options["body"])[:BODY_LIMIT],
            url=settings.ONESIGNAL_SITE_ORIGIN.rstrip("/") + reverse("notifications"),
            available_at=now,
            expires_at=now + PROBE_LIFETIME,
        )
        self.stdout.write(f"Задание в очереди: id={item.pk} key={item.key}")
        wait_seconds = max(0, int(options["wait"]))
        if wait_seconds:
            item = self._wait(item, wait_seconds)
        self.stdout.write(
            f"Статус: {item.status}, попыток {item.attempts}, "
            f"error={item.error_code or '—'}, provider={item.provider_id or '—'}"
        )
        self._print_stats(item, check_provider=bool(options["check_provider"]))
        logger.info(
            "Пробная push-доставка поставлена в очередь",
            delivery_id=item.pk,
            user_id=user.pk,
            status=item.status,
        )
        if item.status != "accepted":
            raise CommandError(
                f"Worker не принял пробный push за {wait_seconds} с: "
                f"статус {item.status}."
            )

    def _wait(self, item: Delivery, seconds: int) -> Delivery:
        """Ждёт решения worker, не удерживая блокировку строки.

        Args:
            item: Только что созданное задание.
            seconds: Верхняя граница ожидания.

        Returns:
            Актуальное состояние задания на момент выхода из ожидания.
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))
            item.refresh_from_db()
            if item.status not in {"queued", "processing"}:
                break
        return item

    def _print_stats(self, item: Delivery, *, check_provider: bool) -> None:
        """Печатает счётчики провайдера, если admin их запросил.

        Args:
            item: Задание с возможным ``provider_id``.
            check_provider: Нужно ли обращаться к OneSignal.
        """
        if not (check_provider and item.provider_id):
            return
        stats = fetch_delivery_stats(item.provider_id)
        if "error" in stats:
            self.stdout.write(f"OneSignal: {stats['error']}")
            return
        self.stdout.write(
            "OneSignal: "
            f"successful={stats.get('successful', '—')} "
            f"failed={stats.get('failed', '—')} "
            f"errored={stats.get('errored', '—')} "
            f"remaining={stats.get('remaining', '—')} "
            f"completed_at={stats.get('completed_at', '—')}"
        )
