#!/bin/bash
set -euo pipefail

# Окружение передаёт Docker; .env никогда не исполняется как shell-код.
python manage.py prepare_deployment

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
