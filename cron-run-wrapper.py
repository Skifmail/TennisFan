#!/usr/local/bin/python
"""Обёртка для django-crontab: восстанавливает env Docker-контейнера."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ENV_PATH = Path("/app/cron.env.json")


def main() -> None:
    """Запустить manage.py с переменными окружения контейнера.

    Args:
        None: Аргументы берутся из ``sys.argv[1:]``.

    Returns:
        None: Завершает процесс с кодом дочерней команды.
    """
    if ENV_PATH.exists():
        with ENV_PATH.open(encoding="utf-8") as fh:
            saved = json.load(fh)
        os.environ.update(saved)
    os.chdir("/app")
    completed = subprocess.run(sys.argv[1:], check=False)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
