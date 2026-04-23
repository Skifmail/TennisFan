#!/bin/bash
set -e

# Dokploy и др. могут писать переменные в .env без передачи в процесс — подгружаем в окружение
if [ -f /app/.env ]; then
  set -a
  # shellcheck source=/dev/null
  source /app/.env
  set +a
fi

echo "Waiting for database..."
while ! python -c "
import os
import sys
import psycopg2
try:
    conn = psycopg2.connect(
        dbname=os.environ.get('POSTGRES_DB'),
        user=os.environ.get('POSTGRES_USER'),
        password=os.environ.get('POSTGRES_PASSWORD'),
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=os.environ.get('POSTGRES_PORT', '5432')
    )
    conn.close()
    exit(0)
except Exception as e:
    print(f'DB connect failed: {e}', file=sys.stderr)
    exit(1)
"; do
    sleep 2
done

echo "Database is ready!"

echo "Running migrations..."
python manage.py migrate --noinput

if [ -n "$DJANGO_SUPERUSER_EMAIL" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  echo "Creating superuser if none exists..."
  python manage.py shell -c "
import os
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(is_superuser=True).exists():
    User.objects.create_superuser(
        email=os.environ['DJANGO_SUPERUSER_EMAIL'],
        password=os.environ['DJANGO_SUPERUSER_PASSWORD']
    )
    print('Superuser created.')
else:
    print('Superuser already exists.')
"
fi

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Setting Telegram webhooks..."
python manage.py set_telegram_webhooks 2>/dev/null || true

echo "Starting server..."
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
