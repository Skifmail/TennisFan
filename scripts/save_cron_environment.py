"""Сохраняет окружение контейнера для django-crontab без shell-подстановок."""

import os
from pathlib import Path

from apps.core.deployment import save_cron_environment

if __name__ == "__main__":
    save_cron_environment(Path("/app/cron.env.json"), dict(os.environ))
