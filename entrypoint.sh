#!/bin/bash
set -euo pipefail

# Окружение передаёт Docker; .env никогда не исполняется как shell-код.
# Миграции и первый суперпользователь выполняет одноразовый сервис release,
# поэтому web стартует сразу и не держит пользователей на странице техработ
# всё время подготовки БД. Без release (локальный запуск) флаг не выставлен.
if [ "${SKIP_DEPLOYMENT_PREPARE:-0}" != "1" ]; then
  python manage.py prepare_deployment
fi

# Сеть Telegram не должна блокировать запуск сайта при каждом рестарте.
# Webhook настраивается отдельной командой после смены URL или токена.
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-3}" \
  --worker-class gthread \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-60}" \
  --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
  --keep-alive "${GUNICORN_KEEPALIVE:-5}" \
  --access-logfile - \
  --error-logfile -
