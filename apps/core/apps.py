"""
Core app configuration.
"""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Ядро"

    def ready(self) -> None:
        """При старте веб-сервера — одноразовое приветствие админам от бота уведомлений."""
        import sys

        skip_commands = ("migrate", "shell", "test", "flush", "loaddata", "dumpdata")
        if any(c in sys.argv for c in skip_commands):
            return
        try:
            from apps.core import telegram_notify

            telegram_notify.send_startup_greeting_to_admins()
        except Exception:
            pass
