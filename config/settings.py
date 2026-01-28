"""Django settings for TennisFan project."""

from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# Загружаем переменные из .env в корне проекта (рядом с manage.py)
from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-tennison-dev-key-change-in-production')

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# ALLOWED_HOSTS — localhost for dev; .up.railway.app matches *.up.railway.app
_allowed = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1,.up.railway.app')
ALLOWED_HOSTS = [h.strip() for h in _allowed.split(',') if h.strip()]

# CSRF: origins must match exactly or use leading-dot subdomain (e.g. https://.up.railway.app).
# For custom domains set CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
_csrf_origins = os.environ.get(
    'CSRF_TRUSTED_ORIGINS',
    'http://localhost:8000,http://127.0.0.1:8000,https://.up.railway.app,https://.railway.app'
)
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins.split(',') if o.strip()]

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    # Local apps
    'apps.core',
    'apps.users',
    'apps.tournaments',
    'apps.courts',
    'apps.sparring',
    'apps.training',
    'apps.content',
    'apps.comments',
    'apps.subscriptions',
    'apps.payments',
    'apps.legal',
    'apps.navigation',
]

# Cloudinary configuration - MUST be set BEFORE any Django initialization
CLOUDINARY_URL = os.environ.get('CLOUDINARY_URL')

# Configure STORAGES FIRST - this must be done before any other imports
if CLOUDINARY_URL:
    # Production: Use Cloudinary for media storage
    STORAGES = {
        'default': {
            'BACKEND': 'cloudinary_storage.storage.MediaCloudinaryStorage',
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        },
    }
else:
    # Development: Use local filesystem storage
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        },
    }

# Now modify INSTALLED_APPS AFTER STORAGES is configured
if CLOUDINARY_URL:
    # Production: Use Cloudinary for media storage
    # NOTE: Only add 'cloudinary' app, NOT 'cloudinary_storage' 
    # cloudinary_storage has compatibility issues with Django 4.2+ STORAGES
    # We configure cloudinary_storage backend directly in STORAGES instead
    INSTALLED_APPS.append('cloudinary')

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                "apps.users.context_processors.unread_notifications",
                "apps.users.context_processors.user_is_coach",
                "apps.navigation.context_processors.nav_menu_items",
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database - SQLite
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Custom User Model
AUTH_USER_MODEL = 'users.User'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
# Note: STATICFILES_STORAGE is now configured in STORAGES dict above (Django 4.2+)

# Ensure static root exists in ephemeral environments (Railway) to avoid missing-dir warnings
STATIC_ROOT.mkdir(parents=True, exist_ok=True)

# Fallback: allow WhiteNoise to use finders if collectstatic didn't run (demo safety)
WHITENOISE_USE_FINDERS = True

# Configure Cloudinary and Media files
if CLOUDINARY_URL:
    # Configure Cloudinary explicitly
    try:
        import cloudinary
        # Parse CLOUDINARY_URL: cloudinary://api_key:api_secret@cloud_name
        url_parts = CLOUDINARY_URL.replace('cloudinary://', '').split('@')
        if len(url_parts) == 2:
            credentials = url_parts[0].split(':')
            cloud_name = url_parts[1]
            if len(credentials) == 2:
                api_key, api_secret = credentials
                cloudinary.config(
                    cloud_name=cloud_name,
                    api_key=api_key,
                    api_secret=api_secret,
                    secure=True
                )
    except Exception:
        # If cloudinary import fails, django-cloudinary-storage will use CLOUDINARY_URL
        pass
    
    # Cloudinary returns absolute URLs
    MEDIA_URL = '/media/'
    # Do NOT set MEDIA_ROOT when using Cloudinary - set to None
    MEDIA_ROOT = None
else:
    # Development: Use local filesystem storage
    MEDIA_URL = '/media/'
    MEDIA_ROOT = BASE_DIR / 'media'
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Login/Logout URLs
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'home'

# Telegram bot for admin notifications (заявки, регистрации, обратная связь)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_ADMIN_CHAT_ID = os.environ.get('TELEGRAM_ADMIN_CHAT_ID', '')
