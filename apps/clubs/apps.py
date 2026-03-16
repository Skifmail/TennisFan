"""
Конфигурация приложения «Клубы».
"""

from django.apps import AppConfig


class ClubsConfig(AppConfig):
    """AppConfig для клубного модуля."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.clubs"
    verbose_name = "Клубы"
