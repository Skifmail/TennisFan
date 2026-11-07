"""Безопасная настройка webhook действующего бота ЛК и исходящего бота администраторов."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from loguru import logger

from apps.telegram_bot.telegram_http import is_telegram_api_enabled
from apps.telegram_bot.transport import TelegramAPIError, telegram_call
from apps.telegram_bot.updates import UPDATE_TYPES


def _api(token: str, method: str, payload: dict[str, Any]) -> None:
    """Передать секреты только в POST-теле и завершить команду при отказе API."""
    try:
        result = telegram_call(token, method, payload, timeout=15)
    except TelegramAPIError as exc:
        logger.warning("Настройка Telegram: method={}, code={}", method, exc.code)
        raise CommandError(
            f"Telegram API: {method} не выполнен ({exc.code})."
        ) from None
    if result is not True:
        raise CommandError(
            f"Telegram API: {method} не подтверждён (telegram_protocol_error)."
        )


def _site_origin() -> str:
    """Разрешить только HTTPS-origin без секретов, произвольных путей и параметров."""
    base = (
        (getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", "") or "").strip().rstrip("/")
    )
    try:
        parts = urlsplit(base)
        valid = (
            parts.scheme == "https"
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and parts.port in (None, 443, 80, 88, 8443)
            and not parts.path
            and not parts.query
            and not parts.fragment
            and not any(char.isspace() for char in base)
        )
    except ValueError:
        valid = False
    if not valid:
        raise CommandError(
            "TELEGRAM_BOT_SITE_BASE_URL должен быть HTTPS-origin без пути, параметров и учётных данных."
        )
    return base


def _same_bot(first_token: str, second_token: str) -> bool:
    """Не сбрасывать webhook пользовательского бота через роль админских уведомлений."""
    if not first_token or not second_token:
        return False
    first_id, first_separator, _ = first_token.partition(":")
    second_id, second_separator, _ = second_token.partition(":")
    return first_token == second_token or bool(
        first_separator
        and second_separator
        and first_id.isascii()
        and first_id.isdecimal()
        and second_id.isascii()
        and second_id.isdecimal()
        and int(first_id) == int(second_id)
    )


class Command(BaseCommand):
    """Проверить конфигурацию до любых изменений и не удалять ожидающие update."""

    help = "Настроить безопасный webhook бота ЛК и сбросить webhook исходящего админского бота."

    def add_arguments(self, parser: CommandParser) -> None:
        """Добавить офлайн-проверку без обращения к Telegram API."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Проверить настройки и показать план без вызова API (даже при TELEGRAM_ENABLED=False).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Настроить только существующие endpoint после проверки всего плана."""
        dry_run = bool(options["dry_run"])
        if not dry_run and not is_telegram_api_enabled():
            self.stdout.write(
                self.style.WARNING(
                    "Telegram API отключён (TELEGRAM_ENABLED=false). Вызовы setWebhook/deleteWebhook пропущены."
                )
            )
            return

        admin_token = (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
        user_token = (getattr(settings, "TELEGRAM_USER_BOT_TOKEN", "") or "").strip()
        user_secret = (
            getattr(settings, "TELEGRAM_USER_BOT_WEBHOOK_SECRET", "") or ""
        ).strip()
        if _same_bot(admin_token, user_token):
            raise CommandError(
                "Для бота ЛК и админских уведомлений нужны разные Telegram-боты."
            )
        user_url = ""
        if user_token:
            if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", user_secret) is None:
                raise CommandError(
                    "TELEGRAM_USER_BOT_WEBHOOK_SECRET обязателен: 1–256 символов A-Z, a-z, 0-9, _ или -."
                )
            user_url = f"{_site_origin()}/telegram/user-bot-webhook/"

        if admin_token:
            if dry_run:
                self.stdout.write(
                    "Бот уведомлений админа: deleteWebhook (ожидающие update сохраняются)."
                )
            else:
                _api(admin_token, "deleteWebhook", {"drop_pending_updates": False})
                self.stdout.write(
                    self.style.SUCCESS("Бот уведомлений админа: webhook сброшен.")
                )
        else:
            self.stdout.write(
                "Бот уведомлений админа: TELEGRAM_BOT_TOKEN не задан, пропуск."
            )

        if user_token:
            if dry_run:
                self.stdout.write(
                    f"Бот ЛК: setWebhook -> {user_url}; секрет проверен, update сохраняются."
                )
            else:
                _api(
                    user_token,
                    "setWebhook",
                    {
                        "url": user_url,
                        "secret_token": user_secret,
                        "allowed_updates": list(UPDATE_TYPES),
                        "drop_pending_updates": False,
                    },
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Бот ЛК: webhook установлен -> {user_url}")
                )
        else:
            self.stdout.write("Бот ЛК: TELEGRAM_USER_BOT_TOKEN не задан, пропуск.")

        # Устаревший /telegram/support-webhook/ отсутствует. Поддержка работает на сайте.
        if dry_run:
            self.stdout.write(self.style.NOTICE("Режим --dry-run: API не вызывался."))
