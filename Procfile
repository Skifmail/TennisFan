release: python manage.py migrate && python manage.py collectstatic --noinput && python manage.py crontab add
web: gunicorn config.wsgi --log-file - --timeout 600
worker_private_chat: bash -lc 'while true; do python manage.py sync_private_chat_access; sleep ${PRIVATE_CHAT_SYNC_INTERVAL_SECONDS:-1800}; done'