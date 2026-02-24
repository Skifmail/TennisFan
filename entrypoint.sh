#!/bin/bash
set -e

echo "Waiting for database..."
while ! python -c "
import os
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
except:
    exit(1)
" 2>/dev/null; do
    sleep 1
done

echo "Database is ready!"

echo "Running migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Setting Telegram webhooks..."
python manage.py set_telegram_webhooks 2>/dev/null || true

echo "Starting server..."
exec gunicorn --bind 0.0.0.0:8000 --workers 2 --threads 4 config.wsgi:application
