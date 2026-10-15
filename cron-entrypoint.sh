#!/bin/bash
set -euo pipefail
umask 077
# Compose запускает cron только после успешной readiness-пробы web.
python -m scripts.save_cron_environment
python manage.py crontab add
exec cron -f
