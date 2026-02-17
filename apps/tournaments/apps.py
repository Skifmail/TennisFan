"""
Tournaments app configuration.
"""

from django.apps import AppConfig


class TournamentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tournaments"
    verbose_name = "Турниры"

    def ready(self):
        # Импортируем сигналы, чтобы они были зарегистрированы
        import apps.tournaments.signals  # noqa: F401
