"""
Проверка отправки в Telegram: загрузка .env, наличие переменных, тестовое сообщение.

Запуск: python manage.py send_telegram_test
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.telegram_notify import send_admin_message


class Command(BaseCommand):
    help = "Проверка Telegram-бота: переменные из .env и тестовая отправка."

    def handle(self, *args, **options):
        token = (getattr(settings, "TELEGRAM_BOT_TOKEN", None) or "").strip()
        chat_id = (getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None) or "").strip()

        self.stdout.write("Переменные берутся из .env в корне проекта (рядом с manage.py).")
        self.stdout.write("Проверка переменных:")
        self.stdout.write(
            f"  TELEGRAM_BOT_TOKEN: {'задан' if token else 'НЕ ЗАДАН'} "
            f"({token[:8]}...)" if token else ""
        )
        self.stdout.write(
            f"  TELEGRAM_ADMIN_CHAT_ID: {'задан' if chat_id else 'НЕ ЗАДАН'} "
            f"({chat_id})" if chat_id else ""
        )

        if not token or not chat_id:
            self.stdout.write(
                self.style.ERROR(
                    "\nДобавьте в .env (в корне проекта, рядом с manage.py):\n"
                    "  TELEGRAM_BOT_TOKEN=123456:ABC...\n"
                    "  TELEGRAM_ADMIN_CHAT_ID=123456789\n"
                    "Без кавычек, без пробелов вокруг =. Перезапустите runserver после правок."
                )
            )
            return

        self.stdout.write("\nОтправка тестового сообщения...")
        ok = send_admin_message("✅ <b>TennisFan</b>: тест уведомлений. Бот работает.")
        if ok:
            self.stdout.write(self.style.SUCCESS("Сообщение отправлено. Проверьте чат в Telegram."))
        else:
            self.stdout.write(
                self.style.ERROR(
                    "Ошибка отправки. Проверьте токен и chat_id. "
                    "Для лички: напишите боту /start, затем получите chat_id через getUpdates."
                )
            )
