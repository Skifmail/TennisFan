"""
Middleware для отложенной инициализации при первом запросе.

Выполняет действия, которые нельзя делать в AppConfig.ready() из-за
RuntimeWarning: Accessing the database during app initialization is discouraged.
"""

import logging
import sys
import time

from django.db import connection

logger = logging.getLogger(__name__)

# Флаг: инициализация уже выполнена (один раз на процесс)
_startup_done = False


def _run_startup_tasks() -> None:
    """
    Обновить Site для sitemap и отправить приветствие админам в Telegram.
    Вызывается один раз при первом HTTP-запросе.
    """
    global _startup_done
    if _startup_done:
        return

    skip_commands = ("migrate", "shell", "test", "flush", "loaddata", "dumpdata")
    if any(c in sys.argv for c in skip_commands):
        _startup_done = True
        return

    try:
        from django.conf import settings
        from django.contrib.sites.models import Site

        site_id = getattr(settings, "SITE_ID", 1)
        domain = getattr(settings, "SITE_DOMAIN", "tennisfan.ru")
        Site.objects.filter(pk=site_id).update(domain=domain, name="TennisFan")
    except Exception as exc:
        logger.debug("Startup: Site update skipped: %s", exc)

    try:
        from apps.core import telegram_notify

        telegram_notify.send_startup_greeting_to_admins()
    except Exception as exc:
        logger.debug("Startup: telegram greeting skipped: %s", exc)

    _startup_done = True


class StartupMiddleware:
    """
    Middleware, выполняющий задачи инициализации при первом HTTP-запросе.

    Переносит логику из AppConfig.ready(), чтобы избежать доступа к БД
    во время загрузки приложений (RuntimeWarning).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _run_startup_tasks()
        return self.get_response(request)


class SlowRequestLoggingMiddleware:
    """Логирует медленные HTTP-запросы для поиска деградаций производительности.

    Args:
        get_response: Django callable следующего middleware/view.

    Returns:
        None: Инициализирует middleware-объект.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._threshold_ms = 500.0

    def __call__(self, request):
        """Замеряет время запроса и пишет предупреждение при превышении порога.

        Args:
            request: Текущий HTTP-запрос.

        Returns:
            HttpResponse: Ответ приложения.
        """
        started_at = time.perf_counter()
        response = self.get_response(request)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        if elapsed_ms >= self._threshold_ms:
            logger.warning(
                "slow_request method=%s path=%s status=%s elapsed_ms=%.2f sql_queries=%s",
                request.method,
                request.path,
                getattr(response, "status_code", "unknown"),
                elapsed_ms,
                len(getattr(connection, "queries", [])),
            )
        return response
