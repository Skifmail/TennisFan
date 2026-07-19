#!/bin/bash
set -e

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

echo "Saving environment for cron jobs..."
python -c "import json, os; json.dump(dict(os.environ), open('/app/cron.env.json', 'w'), ensure_ascii=False)"
chmod 600 /app/cron.env.json

echo "Setting up cron jobs..."
python manage.py crontab add

echo "Starting cron daemon..."
cron -f
