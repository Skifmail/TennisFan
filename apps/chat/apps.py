"""Инициализация городского чата и защитного сохранения архива."""

from django.apps import AppConfig


class ChatConfig(AppConfig):
    """Подключает обработчики жизненного цикла городских комнат.

    Attributes:
        name: Путь приложения.
        default_auto_field: Тип ключей, совместимый с существующими миграциями.
    """

    name = "apps.chat"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Регистрирует обработчики один раз при запуске приложения."""
        from . import archive_signals

        archive_signals.connect_archive_signals()
