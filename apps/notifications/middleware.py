"""Продление привязки push-устройства при любой авторизованной активности."""

import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import Final

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from loguru import logger

from .browser_identity import COOKIE_NAME, COOKIE_SALT
from .models import PushDevice
from .services import center_enabled

EXTENSION_INTERVAL: Final = timedelta(hours=1)
EXTENSION_CACHE_PREFIX: Final = "notifications:device-lifetime"
# Эти маршруты сами блокируют устройство и управляют его сроком/поколением.
SKIP_PATH_PREFIXES: Final = ("/notification-center/",)


class PushDeviceLifetimeMiddleware:
    """Обновляет срок устройства не чаще раза в час на активную сессию.

    Привязка больше не зависит от визита только на страницу уведомлений:
    любое действие авторизованного пользователя продлевает её до срока сессии.
    Троттлинг не даёт делать UPDATE на каждый запрос.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Сохраняет следующий обработчик.

        Args:
            get_response: Следующий middleware или view.
        """
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Продлевает устройство перед обработкой запроса.

        Args:
            request: Текущий запрос.

        Returns:
            Ответ следующего обработчика.
        """
        self._extend(request)
        return self.get_response(request)

    def _extend(self, request: HttpRequest) -> None:
        """Выполняет безопасное обновление срока без влияния на ответ.

        Args:
            request: Запрос с сессией и необязательным cookie устройства.
        """
        if not center_enabled() or request.path.startswith(SKIP_PATH_PREFIXES):
            return
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return
        try:
            token = request.get_signed_cookie(COOKIE_NAME, default="", salt=COOKIE_SALT)
            if not token:
                return
            deadline = request.session.get_expiry_date()
            if deadline <= timezone.now():
                return
            digest = hashlib.sha256(token.encode()).hexdigest()
            cache_key = f"{EXTENSION_CACHE_PREFIX}:{user.pk}:{digest}"
            # Кэш убирает лишний UPDATE-запрос на каждый запрос; при его отказе
            # остаётся условный UPDATE, который сам ограничен сроком устройства.
            try:
                if not cache.add(
                    cache_key, 1, timeout=int(EXTENSION_INTERVAL.total_seconds())
                ):
                    return
            except Exception:
                pass
            try:
                PushDevice.objects.filter(
                    device_hash=digest,
                    user_id=user.pk,
                    expires_at__lt=deadline - EXTENSION_INTERVAL,
                ).update(expires_at=deadline)
            except Exception:
                try:
                    cache.delete(cache_key)
                except Exception:
                    pass
                raise
        except Exception:
            # Сбой продления не должен ломать страницу; следующая активность повторит.
            logger.warning("Не удалось продлить привязку push-устройства")
