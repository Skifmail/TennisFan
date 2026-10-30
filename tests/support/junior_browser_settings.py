"""Изолированный браузерный стенд детской лиги с отдельной временной БД."""

from pathlib import Path
from tempfile import gettempdir

from tests.support import chat_browser_settings as preview

globals().update({key: getattr(preview, key) for key in dir(preview) if key.isupper()})
DEBUG = True
SECRET_KEY = "junior-preview-local-only"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(Path(gettempdir()) / "tennison-juniors-preview.sqlite3"),
    }
}
JUNIOR_LEAGUE_ENABLED = True
JUNIOR_PAYMENTS_ENABLED = False
NOTIFICATION_CENTER_ENABLED = True
NOTIFICATION_PROMPT_ENABLED = False
ONESIGNAL_APP_ID = ""
YANDEX_METRIKA_ID = ""
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:18767", "http://localhost:18767"]
STATIC_ROOT = Path(gettempdir()) / "tennison-juniors-preview-static"
