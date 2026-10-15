"""Изолированные настройки локального браузерного стенда, не production."""

from pathlib import Path
from tempfile import gettempdir

from config import settings as project_settings

globals().update(
    {
        key: getattr(project_settings, key)
        for key in dir(project_settings)
        if key.isupper()
    }
)
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:18765", "http://127.0.0.1:18766"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MIDDLEWARE = [
    item
    for item in project_settings.MIDDLEWARE
    if item != "apps.core.middleware.StartupMiddleware"
]
STATIC_ROOT = Path(gettempdir()) / "tennison-chat-browser-static"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# На стенде файлы перечитываются после повторного collectstatic.
WHITENOISE_AUTOREFRESH = True
