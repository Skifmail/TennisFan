"""Настройка раздела собственных кемпов."""

from django.apps import AppConfig


class CampsConfig(AppConfig):
    """Программы, заезды и участники TennisFan."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.camps"
    verbose_name = "Кемпы TennisFan"
