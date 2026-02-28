"""
Проверка кэша для бота уведомлений и опциональная отправка приветствия.

Позволяет убедиться, что используется общий кэш (Redis), и при необходимости
отправить приветствие админам вручную (например, после деплоя без Redis).

Запуск:
  python manage.py check_telegram_notify_cache           # только проверка
  python manage.py check_telegram_notify_cache --send    # проверка + отправить приветствие
"""

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand

from apps.core.telegram_notify import (
    CACHE_GREETING_TIMEOUT,
    CACHE_KEY_NOTIFY_GREETING_SENT,
    NOTIFY_STARTUP_GREETING,
    _is_shared_cache,
    send_admin_message,
)


class Command(BaseCommand):
    help = (
        "Проверить кэш для бота уведомлений (общий ли Redis). "
        "С флагом --send — отправить приветствие админам вручную."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--send",
            action="store_true",
            help="Отправить приветственное сообщение админам (и записать ключ в кэш).",
        )

    def handle(self, *args, **options):
        backend = settings.CACHES.get("default", {}).get("BACKEND", "")
        self.stdout.write(f"Кэш (default): {backend}")

        is_shared = _is_shared_cache()
        if is_shared:
            self.stdout.write(
                self.style.SUCCESS(
                    "Кэш общий (Redis/Memcached/DB) — приветствие при старте будет уходить один раз."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Кэш локальный (LocMem). Приветствие при старте отключено. "
                    "Чтобы включить: задайте USE_REDIS=True и запустите Redis, либо используйте --send после деплоя."
                )
            )

        # Проверка доступности кэша
        try:
            cache.set("_telegram_check", 1, timeout=5)
            val = cache.get("_telegram_check")
            cache.delete("_telegram_check")
            if val == 1:
                self.stdout.write("Операции с кэшем выполняются успешно.")
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "Кэш может работать некорректно (get не вернул записанное)."
                    )
                )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Ошибка кэша: {e}"))

        if options["send"]:
            if is_shared:
                added = cache.add(
                    CACHE_KEY_NOTIFY_GREETING_SENT,
                    True,
                    timeout=CACHE_GREETING_TIMEOUT,
                )
                if not added:
                    self.stdout.write(
                        self.style.WARNING(
                            "Ключ приветствия уже в кэше (недавно отправляли). Сообщение не отправлено."
                        )
                    )
                    return
            ok = send_admin_message(NOTIFY_STARTUP_GREETING)
            if ok:
                self.stdout.write(self.style.SUCCESS("Приветствие отправлено админам."))
            else:
                self.stdout.write(
                    self.style.ERROR(
                        "Не удалось отправить (проверьте TELEGRAM_BOT_TOKEN и TELEGRAM_ADMIN_CHAT_ID)."
                    )
                )
