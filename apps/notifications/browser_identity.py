"""Публичные константы подписанного cookie и безопасное чтение идентичности браузера."""

import hashlib
from typing import Final

from django.http import HttpRequest

COOKIE_NAME: Final = "notification_device"
COOKIE_SALT: Final = "notification-device"


def device_hash(request: HttpRequest) -> str:
    """Проверяет подпись cookie и возвращает только необратимый хэш.

    Args:
        request: Запрос исходного браузера.

    Returns:
        SHA256 токена либо пустая строка при отсутствующем или неверном cookie.
    """
    token = request.get_signed_cookie(COOKIE_NAME, default="", salt=COOKIE_SALT) or ""
    return hashlib.sha256(token.encode()).hexdigest() if token else ""
