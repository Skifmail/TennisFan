"""
Navigation app configuration.
"""

from django.apps import AppConfig


class NavigationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.navigation"
    verbose_name = "Навигация"

    def ready(self) -> None:
        """Регистрирует сигналы приложения.

        Args:
            None.

        Returns:
            None.
        """
        from . import signals  # noqa: F401
