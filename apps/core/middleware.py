"""
Middleware для отложенной инициализации при первом запросе.

Выполняет действия, которые нельзя делать в AppConfig.ready() из-за
RuntimeWarning: Accessing the database during app initialization is discouraged.
"""

import logging
import sys
import time
from collections.abc import Callable
from typing import Final

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse

HEALTH_PATHS: Final = ("/health/live/", "/health/ready/", "/health/notifications/")

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


def _brand_hosts() -> set[str]:
    """Возвращает основные хосты брендов без префикса ``www``.

    Функция повторяет набор из ``site_branding``: TennisFan из ``SITE_DOMAIN``
    и TennisTop из ``TENNISTOP_EXTRA_HOSTS`` плюс значения по умолчанию.

    Returns:
        Множество нормализованных хостов без ``www.``.
    """
    hosts: set[str] = {"tennistop.ru"}
    site_domain = str(getattr(settings, "SITE_DOMAIN", "")).strip().lower()
    extra_hosts = getattr(settings, "TENNISTOP_EXTRA_HOSTS", None)
    if isinstance(extra_hosts, str):
        extra_hosts = extra_hosts.split(",")
    for raw_host in (site_domain, *(extra_hosts or ())):
        host = str(raw_host).strip().lower().removeprefix("www.")
        if host:
            hosts.add(host)
    return hosts


def _canonical_host_redirects() -> dict[str, str]:
    """Собирает пары «www-хост → основной хост» из настроек.

    Returns:
        Карта редиректов для www-адресов всех хостов брендов.
    """
    return {f"www.{host}": host for host in _brand_hosts()}


class CanonicalHostMiddleware:
    """Уводит www-адреса на канонический хост без потери пути и параметров.

    OneSignal закрепляет подписку за точным origin, поэтому www-копия сайта
    не может использовать тот же App ID. Код 308 сохраняет метод и query.

    Attributes:
        get_response: Следующий обработчик запроса.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Строит карту редиректов из настроек домена и бренда.

        Args:
            get_response: Следующий обработчик запроса.
        """
        self.get_response = get_response
        self._redirects = _canonical_host_redirects()

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Перенаправляет www-хост, не затрагивая пробы здоровья.

        Args:
            request: Текущий HTTP-запрос.

        Returns:
            Постоянный редирект 308 либо обычный ответ приложения.
        """
        if request.path not in HEALTH_PATHS:
            # Сырой Host читаем до проверки ALLOWED_HOSTS: www-копия должна
            # получить редирект даже если её ещё нет в списке разрешённых.
            host = request.META.get("HTTP_HOST", "").split(":", 1)[0].strip().lower()
            target = self._redirects.get(host)
            if target:
                return HttpResponse(
                    status=308,
                    headers={"Location": f"https://{target}{request.get_full_path()}"},
                )
        return self.get_response(request)


class AdminNoCacheMiddleware:
    """Запрещает кэширование страниц админки (устаревший CSRF-токен → 403).

    На мобильных браузерах закэшированная форма добавления корта часто
    отправляет старый csrfmiddlewaretoken; no-store заставляет браузер
    каждый раз загружать свежую страницу.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self._admin_prefix = f"/{settings.ADMIN_URL.strip('/')}/"

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if request.path.startswith(self._admin_prefix):
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response
