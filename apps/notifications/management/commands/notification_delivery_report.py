"""Диагностика push: устройства пользователя и отчёт OneSignal о доставке."""

import json
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from loguru import logger

from apps.notifications.infrastructure.push import fetch_delivery_stats
from apps.notifications.models import Delivery, PushDevice
from apps.notifications.services import preferences


class Command(BaseCommand):
    """Показывает, куда реально ушёл push и что ответил провайдер.

    Attributes:
        help: Описание команды.
    """

    help = (
        "Показать подтверждённые подписки пользователя и итог доставки "
        "последних push-сообщений по данным OneSignal."
    )

    def add_arguments(self, parser: Any) -> None:
        """Добавляет фильтр по пользователю и глубину истории.

        Args:
            parser: Парсер аргументов Django.
        """
        parser.add_argument(
            "--email",
            default="",
            help="Email пользователя; без него показываются последние push всех.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=10,
            help="Сколько последних заданий показать.",
        )
        parser.add_argument(
            "--check-provider",
            action="store_true",
            help="Запросить у OneSignal счётчики доставки по provider_id.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Вывести отчёт одной JSON-строкой.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Собирает отчёт и печатает его в stdout.

        Args:
            *args: Позиционные аргументы Django.
            **options: Email, лимит и флаги отчёта.

        Raises:
            CommandError: Пользователь с указанным email не найден.
        """
        limit = max(1, int(options["limit"]))
        deliveries = Delivery.objects.filter(channel="push").select_related("user")
        user = None
        if options["email"]:
            user = (
                get_user_model()
                .objects.filter(email__iexact=str(options["email"]).strip())
                .first()
            )
            if user is None:
                raise CommandError("Пользователь с таким email не найден.")
            deliveries = deliveries.filter(user=user)
        deliveries = deliveries.order_by("-created_at")[:limit]
        devices = (
            PushDevice.objects.filter(user=user).order_by("-updated_at")
            if user is not None
            else PushDevice.objects.none()
        )
        deliveries_report: list[dict[str, Any]] = []
        report: dict[str, Any] = {
            "user": (
                {
                    "id": user.pk,
                    "email": user.email,
                    "push_enabled": preferences(user.pk)[0],
                    "email_enabled": preferences(user.pk)[1],
                }
                if user is not None
                else None
            ),
            "devices": [
                {
                    "subscription_id": (
                        str(device.subscription_id) if device.subscription_id else None
                    ),
                    "external_id": str(device.external_id),
                    "active": device.active,
                    "expires_at": device.expires_at.isoformat(),
                    "updated_at": device.updated_at.isoformat(),
                }
                for device in devices
            ],
            "deliveries": deliveries_report,
        }
        for item in deliveries:
            stats: dict[str, object] | None = None
            if options["check_provider"] and item.provider_id:
                stats = fetch_delivery_stats(item.provider_id)
            deliveries_report.append(
                {
                    "created_at": item.created_at.isoformat(),
                    "event_key": item.event_key,
                    "user_id": item.user_id,
                    "status": item.status,
                    "attempts": item.attempts,
                    "error_code": item.error_code,
                    "provider_id": item.provider_id,
                    "title": item.title,
                    "url": item.url,
                    "provider": stats,
                }
            )
        if options["json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False))
        else:
            self._print_human(report)
        logger.info(
            "Отчёт о доставке push",
            deliveries=len(report["deliveries"]),
            user_id=user.pk if user is not None else None,
        )

    def _print_human(self, report: dict[str, Any]) -> None:
        """Печатает отчёт в виде, удобном для копирования в поддержку.

        Args:
            report: Собранные данные без секретов и REST-ключа.
        """
        user = report["user"]
        if user is not None:
            self.stdout.write(
                f"Пользователь: {user['email']} (id={user['id']}), "
                f"push={'вкл' if user['push_enabled'] else 'выкл'}, "
                f"email={'вкл' if user['email_enabled'] else 'выкл'}"
            )
        else:
            self.stdout.write("Пользователь не указан: показаны последние push.")
        self.stdout.write(f"Устройства: {len(report['devices'])}")
        for device in report["devices"]:
            self.stdout.write(
                "  подписка "
                f"{device['subscription_id'] or '—'}, "
                f"{'активна' if device['active'] else 'неактивна'}, "
                f"до {device['expires_at']}, "
                f"обновлена {device['updated_at']}"
            )
        self.stdout.write(f"Доставки: {len(report['deliveries'])}")
        for item in report["deliveries"]:
            self.stdout.write(
                f"  {item['created_at']} | {item['event_key']} | "
                f"{item['status']} | попыток {item['attempts']} | "
                f"error={item['error_code'] or '—'} | "
                f"provider={item['provider_id'] or '—'}"
            )
            self.stdout.write(f"    {item['title']} → {item['url']}")
            provider = item["provider"]
            if provider:
                if "error" in provider:
                    self.stdout.write(f"    OneSignal: {provider['error']}")
                else:
                    self.stdout.write(
                        "    OneSignal: "
                        f"successful={provider.get('successful', '—')} "
                        f"failed={provider.get('failed', '—')} "
                        f"errored={provider.get('errored', '—')} "
                        f"remaining={provider.get('remaining', '—')} "
                        f"completed_at={provider.get('completed_at', '—')}"
                    )
