"""Регистрация центра уведомлений."""

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """Объединяет настройки, очередь и рассылки в отдельном разделе админки.

    Attributes:
        name: Путь приложения.
        verbose_name: Название раздела администратора.
    """

    name = "apps.notifications"
    verbose_name = "Уведомления"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Подключает отзыв устройств при выходе из аккаунта."""
        from . import signals  # noqa: F401
