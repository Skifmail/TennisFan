"""Короткие HTTP-пробы без авторизации, мутаций и внешних уведомлений."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from redis import Redis
from redis.exceptions import RedisError

PROBE_TIMEOUT_SECONDS: Final = 2
LIVE_PATH: Final = "/health/live/"
READY_PATH: Final = "/health/ready/"


def _dependencies_ready() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        urls: list[str] = []
        if getattr(settings, "USE_REDIS", False):
            urls.append(settings.REDIS_URL)
        chat_url = getattr(settings, "CHAT_REDIS_URL", "")
        if chat_url and getattr(settings, "HEALTHCHECK_CHAT_REDIS", False):
            urls.append(chat_url)
        for url in dict.fromkeys(urls):
            with Redis.from_url(
                url,
                socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
                socket_timeout=PROBE_TIMEOUT_SECONDS,
            ) as client:
                client.ping()
    except (DatabaseError, RedisError, OSError, ValueError):
        return False
    return True


class HealthMiddleware:
    """Обрабатывает только пробы до запуска остальной цепочки middleware.

    Attributes:
        get_response: Обработчик обычных запросов приложения.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Сохраняет обработчик следующего middleware.

        Args:
            get_response: Следующий обработчик запроса.
        """
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Возвращает безопасный результат пробы либо передаёт запрос дальше.

        Args:
            request: Текущий HTTP-запрос.

        Returns:
            JSON для пробы или обычный ответ приложения.

        Raises:
            DisallowedHost: Заголовок Host не разрешён настройками Django.
        """
        if request.path not in (LIVE_PATH, READY_PATH):
            return self.get_response(request)
        request.get_host()
        if request.method not in ("GET", "HEAD"):
            return HttpResponse(status=405, headers={"Allow": "GET, HEAD"})
        ready = request.path == LIVE_PATH or _dependencies_ready()
        response = JsonResponse(
            {"status": "ok" if ready else "unavailable"}, status=200 if ready else 503
        )
        response["Cache-Control"] = "no-store"
        return response
