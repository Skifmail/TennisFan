"""
Настройка webhook для всех Telegram-ботов проекта.

- Бот уведомлений админа (TELEGRAM_BOT_TOKEN): только отправляет сообщения,
  webhook сбрасывается (deleteWebhook), чтобы не получать обновления.
- Бот ЛК (TELEGRAM_USER_BOT_TOKEN): setWebhook на /telegram/user-bot-webhook/
- Бот поддержки (TELEGRAM_SUPPORT_BOT_TOKEN): setWebhook на /telegram/support-webhook/

Запуск:
  python manage.py set_telegram_webhooks

Требуется TELEGRAM_BOT_SITE_BASE_URL в .env (например https://tennisfan.ru).
"""

import logging
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot"


def _api(token: str, method: str, params: dict | None = None) -> dict:
    url = f"{TELEGRAM_API}{token}/{method}"
    if params:
        url = f"{url}?{urlencode(params)}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json() or {}
    except Exception as e:
        logger.exception("Telegram API %s: %s", method, e)
        return {"ok": False, "description": str(e)}


def _set_webhook(token: str, url: str, secret: str = "") -> bool:
    params = {"url": url}
    if secret:
        params["secret_token"] = secret
    data = _api(token, "setWebhook", params)
    return bool(data.get("ok", False))


def _delete_webhook(token: str) -> bool:
    data = _api(token, "deleteWebhook")
    return bool(data.get("ok", False))


def _get_webhook_info(token: str) -> dict:
    return _api(token, "getWebhookInfo")


class Command(BaseCommand):
    help = "Настроить webhook для бота уведомлений админа, бота ЛК и бота поддержки."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, какие URL будут установлены, без вызова API.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        base = (getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", None) or "").rstrip("/")
        if not base:
            self.stderr.write(
                self.style.ERROR(
                    "TELEGRAM_BOT_SITE_BASE_URL не задан. Укажите в .env (например https://tennisfan.ru)."
                )
            )
            return

        # 1. Бот уведомлений админа — только отправка, webhook сбрасываем
        admin_token = (getattr(settings, "TELEGRAM_BOT_TOKEN", None) or "").strip()
        if admin_token:
            if dry_run:
                self.stdout.write(
                    "Бот уведомлений админа (TELEGRAM_BOT_TOKEN): будет вызван deleteWebhook"
                )
            else:
                if _delete_webhook(admin_token):
                    self.stdout.write(
                        self.style.SUCCESS(
                            "Бот уведомлений админа: webhook сброшен (deleteWebhook)."
                        )
                    )
                else:
                    self.stderr.write(
                        self.style.WARNING(
                            "Бот уведомлений админа: не удалось сбросить webhook (проверьте токен)."
                        )
                    )
        else:
            self.stdout.write(
                "Бот уведомлений админа: TELEGRAM_BOT_TOKEN не задан, пропуск."
            )

        # 2. Бот ЛК (user bot)
        user_token = (getattr(settings, "TELEGRAM_USER_BOT_TOKEN", None) or "").strip()
        user_url = f"{base}/telegram/user-bot-webhook/"
        user_secret = (
            getattr(settings, "TELEGRAM_USER_BOT_WEBHOOK_SECRET", None) or ""
        ).strip()
        if user_token:
            if dry_run:
                self.stdout.write(
                    f"Бот ЛК (TELEGRAM_USER_BOT_TOKEN): setWebhook -> {user_url}"
                )
            else:
                if _set_webhook(user_token, user_url, user_secret):
                    self.stdout.write(
                        self.style.SUCCESS(f"Бот ЛК: webhook установлен -> {user_url}")
                    )
                else:
                    info = _get_webhook_info(user_token)
                    self.stderr.write(
                        self.style.WARNING(
                            f"Бот ЛК: setWebhook не удался. getWebhookInfo: {info}"
                        )
                    )
        else:
            self.stdout.write("Бот ЛК: TELEGRAM_USER_BOT_TOKEN не задан, пропуск.")

        # 3. Бот поддержки
        support_token = (
            getattr(settings, "TELEGRAM_SUPPORT_BOT_TOKEN", None) or ""
        ).strip()
        support_url = f"{base}/telegram/support-webhook/"
        support_secret = (
            getattr(settings, "TELEGRAM_SUPPORT_WEBHOOK_SECRET", None) or ""
        ).strip()
        if support_token:
            if dry_run:
                self.stdout.write(
                    f"Бот поддержки (TELEGRAM_SUPPORT_BOT_TOKEN): setWebhook -> {support_url}"
                )
            else:
                if _set_webhook(support_token, support_url, support_secret):
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Бот поддержки: webhook установлен -> {support_url}"
                        )
                    )
                else:
                    info = _get_webhook_info(support_token)
                    self.stderr.write(
                        self.style.WARNING(
                            f"Бот поддержки: setWebhook не удался. getWebhookInfo: {info}"
                        )
                    )
        else:
            self.stdout.write(
                "Бот поддержки: TELEGRAM_SUPPORT_BOT_TOKEN не задан, пропуск."
            )

        if dry_run:
            self.stdout.write(self.style.NOTICE("Режим --dry-run: API не вызывался."))
