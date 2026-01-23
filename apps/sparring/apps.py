"""
Sparring app configuration.
"""
from django.apps import AppConfig


class SparringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sparring"
    verbose_name = "Спарринг"
