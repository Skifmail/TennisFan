"""
Django settings for TennisFan project.
Production-ready configuration.
"""

import base64
import hashlib
import os
import sys
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
# .env.local с override только в разработке, чтобы не затирать переменные из Dokploy/systemd на сервере
if os.environ.get("DEBUG", "False").strip().lower() == "true":
    load_dotenv(BASE_DIR / ".env.local", override=True)

# ------------------------------------------------------------------------------
# CORE
# ------------------------------------------------------------------------------

DEBUG = os.environ.get("DEBUG", "False") == "True"
# manage.py test передаёт «test» в argv; pytest — нет, иначе включается manifest staticfiles.
_TEST_RUNNER = Path(sys.argv[0]).name
TESTING = "test" in sys.argv or _TEST_RUNNER in ("pytest", "py.test")
PROFILING = os.environ.get("PROFILING", "False") == "True"

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-secret-key-change-in-production"
    else:
        raise ImproperlyConfigured("SECRET_KEY must be set in environment")

# ------------------------------------------------------------------------------
# ALLOWED HOSTS / CSRF
# ------------------------------------------------------------------------------

_allowed = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1")
ALLOWED_HOSTS = [h.strip() for h in _allowed.split(",") if h.strip()]

_csrf = os.environ.get(
    "CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
)
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf.split(",") if o.strip()]

# На мобильных 403 CSRF часто из‑за кэша страницы (устаревший токен) или из‑за www/non-www.
# Убедитесь, что в CSRF_TRUSTED_ORIGINS есть и https://tennisfan.ru и https://www.tennisfan.ru.

# Дополнительные хосты для бренда TennisTop (через запятую). По умолчанию: tennistop.ru, www.tennistop.ru.
_tennistop_extra = os.environ.get("TENNISTOP_EXTRA_HOSTS", "").strip()
TENNISTOP_EXTRA_HOSTS: list[str] | str | None = (
    _tennistop_extra if _tennistop_extra else None
)

# ------------------------------------------------------------------------------
# SECURITY
# ------------------------------------------------------------------------------

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

SESSION_COOKIE_AGE = 1209600
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = (
    os.environ.get("SESSION_SAVE_EVERY_REQUEST", "False") == "True"
)

# Защита от перебора паролей (админка и вход на сайт)
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=5)  # время блокировки после превышения попыток
AXES_LOCKOUT_TEMPLATE = "axes/lockout.html"
AXES_LOCKOUT_URL = None

# Опционально: свой путь к админке (в .env задать, напр. ADMIN_URL=manage-secret-xyz)
ADMIN_URL = os.environ.get("ADMIN_URL", "admin").strip("/") or "admin"

# ------------------------------------------------------------------------------
# APPLICATIONS
# ------------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.sitemaps",
    "django.contrib.humanize",
    "django.contrib.postgres",
    "django_crontab",
    "django_extensions",
    "storages",
    "axes",
    # Local apps
    "apps.core",
    "apps.users",
    "apps.tournaments",
    "apps.clubs",
    "apps.courts",
    "apps.sparring",
    "apps.training",
    "apps.content",
    "apps.comments",
    "apps.subscriptions",
    "apps.payments",
    "apps.legal",
    "apps.navigation",
    "apps.shop",
    "apps.telegram_bot",
    "apps.player_ratings",
]

if PROFILING and DEBUG:
    INSTALLED_APPS += [
        "debug_toolbar",
        "silk",
    ]

# ------------------------------------------------------------------------------
# MIDDLEWARE
# ------------------------------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
    "apps.core.middleware.StartupMiddleware",
]

if PROFILING and DEBUG:
    MIDDLEWARE = [
        "debug_toolbar.middleware.DebugToolbarMiddleware",
        "silk.middleware.SilkyMiddleware",
        *MIDDLEWARE,
    ]
MIDDLEWARE.append("apps.core.middleware.SlowRequestLoggingMiddleware")

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# ------------------------------------------------------------------------------
# TEMPLATES
# ------------------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.users.context_processors.unread_notifications",
                "apps.users.context_processors.user_is_coach",
                "apps.navigation.context_processors.nav_menu_items",
                "apps.core.context_processors.telegram_community_url",
                "apps.core.context_processors.footer_social_links",
                "apps.core.context_processors.search_engine_verification",
                "apps.core.context_processors.site_meta",
                "apps.core.context_processors.site_branding",
                "apps.core.context_processors.platform_activity_unseen",
                "apps.clubs.context_processors.club_context",
            ],
        },
    },
]

# ------------------------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------------------------

if os.environ.get("USE_POSTGRES", "False") == "True":
    required = ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
    for var in required:
        if not os.environ.get(var):
            raise ImproperlyConfigured(f"{var} is required when USE_POSTGRES=True")

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB"),
            "USER": os.environ.get("POSTGRES_USER"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD"),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": int(os.environ.get("CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {
                "connect_timeout": int(os.environ.get("POSTGRES_CONNECT_TIMEOUT", "5")),
            },
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "db.sqlite3"),
        }
    }

AUTH_USER_MODEL = "users.User"

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Валидаторы паролей (в т.ч. для суперпользователя и staff)
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ------------------------------------------------------------------------------
# STATIC FILES
# ------------------------------------------------------------------------------

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if TESTING
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    }
}

# ------------------------------------------------------------------------------
# MEDIA STORAGE (Spaceweb S3 или локально)
# ------------------------------------------------------------------------------

if os.environ.get("USE_S3", "False") == "True":
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
    }

    AWS_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY")
    AWS_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_KEY")
    AWS_STORAGE_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
    AWS_S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
    AWS_S3_REGION_NAME = os.environ.get("S3_REGION", "ru-1")

    AWS_DEFAULT_ACL = "public-read"
    AWS_S3_FILE_OVERWRITE = False
    AWS_QUERYSTRING_AUTH = False
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_ADDRESSING_STYLE = "path"

    MEDIA_URL = f"{AWS_S3_ENDPOINT_URL}/{AWS_STORAGE_BUCKET_NAME}/"

else:
    STORAGES["default"] = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    }

    MEDIA_URL = "/media/"
    MEDIA_ROOT = BASE_DIR / "media"
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------------------
# CACHE (Redis optional)
# ------------------------------------------------------------------------------

USE_REDIS = os.environ.get("USE_REDIS", "False") == "True"
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/1")
CACHE_KEY_PREFIX = os.environ.get("CACHE_KEY_PREFIX", "tennison")
CACHE_DEFAULT_TIMEOUT = int(os.environ.get("CACHE_DEFAULT_TIMEOUT", "300"))
CACHE_SOCKET_CONNECT_TIMEOUT = float(
    os.environ.get("CACHE_SOCKET_CONNECT_TIMEOUT", "2.0")
)
CACHE_SOCKET_TIMEOUT = float(os.environ.get("CACHE_SOCKET_TIMEOUT", "2.0"))

if USE_REDIS:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "TIMEOUT": CACHE_DEFAULT_TIMEOUT,
            "KEY_PREFIX": CACHE_KEY_PREFIX,
            "OPTIONS": {
                "socket_connect_timeout": CACHE_SOCKET_CONNECT_TIMEOUT,
                "socket_timeout": CACHE_SOCKET_TIMEOUT,
            },
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "TIMEOUT": CACHE_DEFAULT_TIMEOUT,
            "KEY_PREFIX": CACHE_KEY_PREFIX,
        }
    }

# По умолчанию не храним пользовательские сессии в вытесняемом кэше.
# При необходимости можно явно задать SESSION_ENGINE через env.
SESSION_ENGINE = os.environ.get(
    "SESSION_ENGINE",
    "django.contrib.sessions.backends.signed_cookies",
)
if SESSION_ENGINE == "django.contrib.sessions.backends.cache":
    SESSION_CACHE_ALIAS = "default"

# ------------------------------------------------------------------------------
# EMAIL
# ------------------------------------------------------------------------------

EMAIL_BACKEND_INNER = os.environ.get(
    "EMAIL_BACKEND",
    (
        "django.core.mail.backends.console.EmailBackend"
        if DEBUG
        else "django.core.mail.backends.smtp.EmailBackend"
    ),
)
# Не допускаем рекурсию, если в env уже указан LoggingEmailBackend.
if EMAIL_BACKEND_INNER == "apps.core.mail.LoggingEmailBackend":
    EMAIL_BACKEND_INNER = (
        "django.core.mail.backends.console.EmailBackend"
        if DEBUG
        else "django.core.mail.backends.smtp.EmailBackend"
    )
EMAIL_BACKEND = "apps.core.mail.LoggingEmailBackend"

EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "False") == "True"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@tennisfan.ru")
USER_NOTIFICATIONS_EMAIL_ENABLED = (
    os.environ.get("USER_NOTIFICATIONS_EMAIL_ENABLED", "True") == "True"
)
ADMIN_NOTIFICATIONS_EMAIL = os.environ.get(
    "ADMIN_NOTIFICATIONS_EMAIL", "admin@tennisfan.ru"
)
COURT_APPLICATION_NOTIFICATION_EMAIL = os.environ.get(
    "COURT_APPLICATION_NOTIFICATION_EMAIL", "admin@tennisfan.ru"
)
SERVER_EMAIL = DEFAULT_FROM_EMAIL
SUPPORT_EMAIL_TIMEOUT_SECONDS = int(
    os.environ.get("SUPPORT_EMAIL_TIMEOUT_SECONDS", "3")
)

# ------------------------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_telegram_admin_raw = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
TELEGRAM_ADMIN_CHAT_ID = _telegram_admin_raw.strip()
# Список chat_id для рассылки уведомлений и поддержки (через запятую в .env)
TELEGRAM_ADMIN_CHAT_IDS = [
    cid.strip() for cid in _telegram_admin_raw.split(",") if cid.strip()
]
TELEGRAM_USER_BOT_TOKEN = os.environ.get("TELEGRAM_USER_BOT_TOKEN", "")
TELEGRAM_USER_BOT_WEBHOOK_SECRET = os.environ.get(
    "TELEGRAM_USER_BOT_WEBHOOK_SECRET", ""
)
# Задайте @username бота без запроса getMe к API (удобно при блокировке api.telegram.org).
TELEGRAM_USER_BOT_USERNAME = os.environ.get("TELEGRAM_USER_BOT_USERNAME", "").strip()
TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID = os.environ.get(
    "TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID", ""
)
# Публичная ссылка на открытое сообщество TennisFan в Telegram (при открытых группах)
TELEGRAM_PUBLIC_COMMUNITY_URL = (
    os.environ.get("TELEGRAM_PUBLIC_COMMUNITY_URL", "https://t.me/TennisFanu").strip()
    or "https://t.me/TennisFanu"
)
TELEGRAM_BOT_SITE_BASE_URL = os.environ.get("TELEGRAM_BOT_SITE_BASE_URL", "")
# Исходящий прокси только для Telegram Bot API (если api.telegram.org недоступен напрямую).
TELEGRAM_API_PROXY_URL = os.environ.get("TELEGRAM_API_PROXY_URL", "").strip()
# Глобально отключить исходящие запросы к Telegram Bot API (боты, уведомления, webhooks-команда).
# При False токены могут оставаться в окружении, но сеть к api.telegram.org не используется.
TELEGRAM_ENABLED = os.environ.get("TELEGRAM_ENABLED", "True").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
SITE_URL = os.environ.get("SITE_URL", "").strip()

# ------------------------------------------------------------------------------
# ЮKassa (YooKassa)
# ------------------------------------------------------------------------------
# Идентификатор магазина и секретный ключ из личного кабинета ЮKassa.
# Документация: https://yookassa.ru/developers/payment-acceptance/getting-started/quick-start
YOOKASSA_SHOP_ID = os.environ.get("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.environ.get("YOOKASSA_SECRET_KEY", "")

# ------------------------------------------------------------------------------
# CLUB PAYMENTS ENCRYPTION
# ------------------------------------------------------------------------------
CLUB_PAYMENT_ENCRYPTION_KEY = os.environ.get("CLUB_PAYMENT_ENCRYPTION_KEY", "").strip()
if not CLUB_PAYMENT_ENCRYPTION_KEY and SECRET_KEY:
    # Генерируем детерминированный ключ Fernet из SECRET_KEY,
    # чтобы в dev не требовать отдельной переменной окружения.
    CLUB_PAYMENT_ENCRYPTION_KEY = base64.urlsafe_b64encode(
        hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
    ).decode("ascii")

# ------------------------------------------------------------------------------
# SITES (для sitemap.xml — домен берётся из модели Site, id=1)
# ------------------------------------------------------------------------------
SITE_ID = 1
# Домен для sitemap (по умолчанию первый из ALLOWED_HOSTS или tennisfan.ru)
SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "").strip() or "tennisfan.ru"
if not SITE_URL:
    SITE_URL = f"https://{SITE_DOMAIN}"

# ------------------------------------------------------------------------------
# Верификация в поисковиках (Google Search Console, Yandex Webmaster)
# ------------------------------------------------------------------------------
# Коды из интерфейсов верификации — подставляются в <meta> в base.html.
# Не заданы — теги не выводятся.
GOOGLE_SITE_VERIFICATION = os.environ.get("GOOGLE_SITE_VERIFICATION", "").strip()
YANDEX_VERIFICATION = os.environ.get("YANDEX_VERIFICATION", "").strip()

# ------------------------------------------------------------------------------
# YANDEX MAPS / GEOCODER
# ------------------------------------------------------------------------------

# Эти настройки читаются в courts.admin/_get_geocoder_api_key и courts.geocoder.
# В продакшене ключи передаются через переменные окружения:
#   YANDEX_MAPS_API_KEY
#   YANDEX_GEOCODER_API_KEY
#   YANDEX_GEOCODER_REFERER
#
# Здесь мы просто прокидываем их в Django settings, чтобы getattr(settings, ...)
# возвращал корректные значения.
YANDEX_MAPS_API_KEY = os.environ.get("YANDEX_MAPS_API_KEY", "")
YANDEX_GEOCODER_API_KEY = os.environ.get("YANDEX_GEOCODER_API_KEY", "")
YANDEX_GEOCODER_REFERER = os.environ.get("YANDEX_GEOCODER_REFERER", "")

# ------------------------------------------------------------------------------
# CRON
# ------------------------------------------------------------------------------
# В Docker cron-задачи не наследуют env контейнера; cron-run-wrapper.py + cron.env.json
# (см. cron-entrypoint.sh) и вывод в stdout контейнера.
# Явный путь к python обязателен: у cron минимальный PATH, ``env python`` падает.
CRONTAB_COMMAND_PREFIX = f"{sys.executable} {BASE_DIR / 'cron-run-wrapper.py'}"
CRONTAB_COMMAND_SUFFIX = ">> /proc/1/fd/1 2>> /proc/1/fd/2"

CRONJOBS = [
    (
        "*/10 * * * *",
        "django.core.management.call_command",
        ["generate_brackets_past_deadlines"],
    ),
    (
        "*/10 * * * *",
        "django.core.management.call_command",
        ["process_tournament_postpayment_window"],
    ),
    (
        "0 */6 * * *",
        "django.core.management.call_command",
        ["fan_process_overdue_matches"],
    ),
    (
        "0 */6 * * *",
        "django.core.management.call_command",
        ["olympic_process_overdue_matches"],
    ),
    (
        "0 */6 * * *",
        "django.core.management.call_command",
        ["round_robin_process_overdue_matches"],
    ),
    (
        "*/15 * * * *",
        "django.core.management.call_command",
        ["auto_accept_stale_proposals"],
    ),
    ("0 3 1 5,10 *", "django.core.management.call_command", ["reset_season_points"]),
    ("0 9 * * *", "django.core.management.call_command", ["send_deadline_reminders"]),
    (
        "0 8 * * *",
        "django.core.management.call_command",
        ["send_tournament_start_reminders"],
    ),
    (
        "*/30 * * * *",
        "django.core.management.call_command",
        ["sync_private_chat_access"],
    ),
    ("0 3 1 * *", "django.core.management.call_command", ["monthly_rating_publish"]),
    (
        "0 4 * * *",
        "django.core.management.call_command",
        ["run_recurring_subscription_payments"],
    ),
    (
        "5 4 * * *",
        "django.core.management.call_command",
        ["run_recurring_club_fee_payments"],
    ),
    (
        "10 4 * * *",
        "django.core.management.call_command",
        ["run_recurring_club_plan_payments"],
    ),
    (
        "0 10 * * *",
        "django.core.management.call_command",
        ["send_subscription_expiry_reminders"],
    ),
    (
        "0 9 * * *",
        "django.core.management.call_command",
        ["send_club_fee_reminders"],
    ),
    (
        "0 9 * * *",
        "django.core.management.call_command",
        ["send_club_subscription_reminders"],
    ),
    (
        "0 10 * * *",
        "django.core.management.call_command",
        ["send_club_tournament_reminders"],
    ),
    (
        "0 2 * * *",
        "django.core.management.call_command",
        ["suspend_expired_clubs"],
    ),
    (
        "0 3 * * *",
        "django.core.management.call_command",
        ["cleanup_suspended_clubs"],
    ),
]

# ------------------------------------------------------------------------------
# MISC
# ------------------------------------------------------------------------------

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"

# ------------------------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------------------------
# Вывод в терминал при DEBUG или LOG_TO_CONSOLE=True (для отладки NTRP и др.)
_LOG_TO_CONSOLE = DEBUG or (os.environ.get("LOG_TO_CONSOLE", "False").lower() == "true")
_ROOT_HANDLERS = ["file_info", "file_warnings", "file_errors"]
_APPS_HANDLERS = ["file_info", "file_warnings", "file_errors"]
if _LOG_TO_CONSOLE:
    _ROOT_HANDLERS = _APPS_HANDLERS = [
        "console",
        "file_info",
        "file_warnings",
        "file_errors",
    ]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_false": {
            "()": "django.utils.log.RequireDebugFalse",
        },
    },
    "handlers": {
        "file_errors": {
            "level": "ERROR",
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": BASE_DIR / "logs" / "django_errors.log",
            "when": "midnight",
            "interval": 1,
            "backupCount": 7,  # Хранить 7 дней
            "formatter": "verbose",
            "encoding": "utf-8",
        },
        "file_warnings": {
            "level": "WARNING",
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": BASE_DIR / "logs" / "django_warnings.log",
            "when": "midnight",
            "interval": 1,
            "backupCount": 7,
            "formatter": "verbose",
            "encoding": "utf-8",
        },
        "file_info": {
            "level": "INFO",
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": BASE_DIR / "logs" / "django_info.log",
            "when": "midnight",
            "interval": 1,
            "backupCount": 7,
            "formatter": "verbose",
            "encoding": "utf-8",
        },
        "console": {
            "level": "DEBUG" if DEBUG else "INFO",
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": _ROOT_HANDLERS,
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": _ROOT_HANDLERS,
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["file_errors"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["file_warnings", "file_errors"],
            "level": "WARNING",
            "propagate": False,
        },
        "apps.tournaments": {
            "handlers": _APPS_HANDLERS,
            "level": "INFO",
            "propagate": False,
        },
        "apps.users": {
            "handlers": _APPS_HANDLERS,
            "level": "INFO",
            "propagate": False,
        },
        "apps.sparring": {
            "handlers": _APPS_HANDLERS,
            "level": "INFO",
            "propagate": False,
        },
        "apps.telegram_bot": {
            "handlers": _APPS_HANDLERS,
            "level": "INFO",
            "propagate": False,
        },
        "apps.core": {
            "handlers": _APPS_HANDLERS,
            "level": "INFO",
            "propagate": False,
        },
        "apps.clubs": {
            "handlers": _APPS_HANDLERS,
            "level": "INFO",
            "propagate": False,
        },
    },
}

# Создаем директорию для логов, если её нет
(BASE_DIR / "logs").mkdir(exist_ok=True)
