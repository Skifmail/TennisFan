# Стек технологий (TennisFan / Tennison)

## Язык и фреймворк

- **Python** 3.11+ (указано в pyproject.toml: `tool.black`, `tool.mypy`)
- **Django** 4.2+ (requirements.txt)
- **WSGI**: `config.wsgi.application`, продакшен: **Gunicorn**

## Зависимости (requirements.txt)

| Пакет | Назначение |
|-------|------------|
| Django>=4.2 | Фреймворк |
| Pillow>=10.0 | Обработка изображений |
| gunicorn>=21.2.0 | ASGI/WSGI сервер |
| python-decouple>=3.8 | Конфиг из env |
| whitenoise>=6.6.0 | Раздача статики |
| python-dateutil>=2.8.2 | Даты |
| markdown>=3.5.0 | Markdown в контенте |
| requests>=2.28.0 | HTTP-клиент |
| python-dotenv>=1.0.0 | .env |
| django-crontab>=0.7.1 | Планировщик задач |
| loguru>=0.7.0 | Логирование |
| psycopg2-binary>=2.9.9 | PostgreSQL |
| django-storages>=1.14.0, boto3>=1.34.0 | S3-хранилище медиа |
| django-axes[ipware]>=6.0.0 | Защита от брутфорса |
| django-extensions>=3.2.0 | Утилиты (runserver_plus и др.) |

## База данных

- **PostgreSQL** (продакшен): `USE_POSTGRES=True`, переменные `POSTGRES_*`
- **SQLite** (по умолчанию для разработки): `db.sqlite3`
- Модель пользователя: `AUTH_USER_MODEL = "users.User"`

## Кэш

- **Redis** (опционально): `USE_REDIS=True`, `REDIS_URL`
- Иначе: `LocMemCache`

## Медиа и статика

- **Статика**: `static/`, сбор в `staticfiles/`, раздача через **WhiteNoise**
- **Медиа**: опционально **S3** (`USE_S3=True`, `S3_*`), иначе локальная папка `media/`

## Внешние сервисы (из config/settings.py)

- **Email**: SMTP или console backend
- **Telegram**: боты (TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_CHAT_IDS, support bot, user bot), вебхуки
- **ЮKassa (YooKassa)**: оплата (YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
- **Яндекс**: карты и геокодер (YANDEX_MAPS_API_KEY, YANDEX_GEOCODER_*)

## Инструменты разработки

- **black** (line-length 88)
- **ruff** (линтер, fix=true)
- **mypy** (strict-ориентированные настройки, часть модулей с отключёнными кодами)

## Переменные окружения (ключевые)

- `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`
- `USE_POSTGRES`, `POSTGRES_*`
- `USE_S3`, `S3_*`
- `USE_REDIS`, `REDIS_URL`
- `ADMIN_URL` — кастомный путь к админке
- `TELEGRAM_*`, `YOOKASSA_*`, `YANDEX_*`
- `SITE_DOMAIN`, `GOOGLE_SITE_VERIFICATION`, `YANDEX_VERIFICATION`

Файлы конфигурации: `.env`, в разработке — `.env.local` (override).
