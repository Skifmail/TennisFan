release: python manage.py migrate && python manage.py collectstatic --noinput && python manage.py crontab add
web: uvicorn config.asgi:application --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*' --ws websockets-sansio --ws-ping-interval 20 --ws-ping-timeout 30 --lifespan off --timeout-graceful-shutdown 30
worker_private_chat: bash -lc 'while true; do python manage.py sync_private_chat_access; sleep ${PRIVATE_CHAT_SYNC_INTERVAL_SECONDS:-1800}; done'
