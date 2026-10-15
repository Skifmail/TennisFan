"""Контейнерная HTTP-проба, проверяющая реальный web/ASGI-процесс."""

from __future__ import annotations

import json
import os
import sys
from typing import Final
from urllib.error import URLError
from urllib.request import Request, urlopen

REQUEST_TIMEOUT_SECONDS: Final = 8
MAX_RESPONSE_BYTES: Final = 1024
DEFAULT_PORT: Final = 8000


def check_health(port: int) -> bool:
    """Проверяет готовность локального HTTP-процесса и его зависимостей.

    Args:
        port: Внутренний порт Gunicorn либо Daphne.

    Returns:
        Истина, только если процесс вернул успешный результат readiness.
    """
    host = os.environ.get("ALLOWED_HOSTS", "127.0.0.1").split(",")[0].strip()
    if not host or host.startswith(("*", ".")):
        host = "127.0.0.1"
    request = Request(
        f"http://127.0.0.1:{port}/health/ready/",
        headers={"Host": host, "X-Forwarded-Proto": "https"},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(MAX_RESPONSE_BYTES))
            return bool(response.status == 200 and payload == {"status": "ok"})
    except (URLError, OSError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(
        0
        if check_health(int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT)
        else 1
    )
