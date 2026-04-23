"""
Users app configuration.
"""

from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"
    verbose_name = "Пользователи"

    def ready(self) -> None:
        """Регистрирует сигналы users.

        Args:
            None.

        Returns:
            None.
        """
        from . import signals  # noqa: F401
