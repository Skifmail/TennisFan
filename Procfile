release: python manage.py migrate && python manage.py collectstatic --noinput && python manage.py crontab add
web: gunicorn config.wsgi --log-file - --timeout 600
