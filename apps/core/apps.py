"""
Core app configuration.
"""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Ядро"

    def ready(self) -> None:
        """
        Регистрация сигналов и т.п.
        Инициализация Site и Telegram-приветствие перенесены в StartupMiddleware,
        чтобы избежать RuntimeWarning при доступе к БД во время загрузки приложений.
        """
        from . import signals  # noqa: F401
        from .activity_signals import register as register_activity_signals

        register_activity_signals()
