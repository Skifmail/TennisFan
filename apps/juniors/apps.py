"""Регистрация единого раздела детской лиги."""

from django.apps import AppConfig


class JuniorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.juniors"
    verbose_name = "Детская лига"
