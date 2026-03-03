"""
Core app configuration.
"""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Ядро"

    def ready(self) -> None:
        """При старте: обновить Site для sitemap, приветствие админам от бота."""
        import sys

        skip_commands = ("migrate", "shell", "test", "flush", "loaddata", "dumpdata")
        if any(c in sys.argv for c in skip_commands):
            return
        try:
            from django.conf import settings
            from django.contrib.sites.models import Site

            site_id = getattr(settings, "SITE_ID", 1)
            domain = getattr(settings, "SITE_DOMAIN", "tennisfan.ru")
            Site.objects.filter(pk=site_id).update(domain=domain, name="TennisFan")
        except Exception:
            pass
        try:
            from apps.core import telegram_notify

            telegram_notify.send_startup_greeting_to_admins()
        except Exception:
            pass
