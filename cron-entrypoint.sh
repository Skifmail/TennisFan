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

echo "Setting up cron jobs..."
python manage.py crontab add

echo "Starting cron daemon..."
cron -f
