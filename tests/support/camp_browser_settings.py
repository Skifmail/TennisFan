"""Изолированная локальная БД для проверки кемпов без внешних писем и платежей."""

from pathlib import Path
from tempfile import gettempdir

from tests.support import chat_browser_settings as preview

globals().update({key: getattr(preview, key) for key in dir(preview) if key.isupper()})
DEBUG = True
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(Path(gettempdir()) / "tennison-camps-preview.sqlite3"),
    }
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
NOTIFICATION_CENTER_ENABLED = True
NOTIFICATION_PROMPT_ENABLED = False
ONESIGNAL_APP_ID = ""
YANDEX_METRIKA_ID = ""
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:18768", "http://localhost:18768"]
