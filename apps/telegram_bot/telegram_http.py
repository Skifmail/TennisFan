"""
Общие параметры HTTP для запросов к Telegram Bot API.

Используется при необходимости прокси (например, если ``api.telegram.org`` недоступен напрямую).
"""

from __future__ import annotations

import os

from django.conf import settings

# Совпадает с логикой ``TELEGRAM_ENABLED`` в ``config/settings.py``
_POSITIVE_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _enabled_from_string(raw: str) -> bool:
    """Интерпретировать строковое значение флага как включён/выключен."""
    return raw.strip().lower() in _POSITIVE_TRUTHY


def is_telegram_api_enabled() -> bool:
    """
    Разрешены ли исходящие HTTP-вызовы к Telegram Bot API в этом процессе.

    Сначала читается ``os.environ['TELEGRAM_ENABLED']``, если ключ задан (в т.ч. пустая строка),
    чтобы не зависеть от порядка загрузки settings и от ошибок вида ``bool("False") == True``.

    При значении ``False`` (переменная окружения ``TELEGRAM_ENABLED``) приложение
    не обращается к ``api.telegram.org``, чтобы избежать таймаутов при блокировках.

    Returns:
        bool: ``True``, если вызовы к API разрешены; иначе ``False``.
    """
    if "TELEGRAM_ENABLED" in os.environ:
        return _enabled_from_string(os.environ["TELEGRAM_ENABLED"])

    configured = getattr(settings, "TELEGRAM_ENABLED", True)
    if isinstance(configured, bool):
        return configured
    if isinstance(configured, str):
        return _enabled_from_string(configured)
    return bool(configured)


def telegram_requests_proxies() -> dict[str, str] | None:
    """
    Вернуть словарь ``proxies`` для библиотеки ``requests`` при настроенном прокси.

    Значение берётся из ``settings.TELEGRAM_API_PROXY_URL`` (переменная окружения
    ``TELEGRAM_API_PROXY_URL``). Поддерживаются URL вида ``http://``, ``https://``,
    ``socks5://`` (для SOCKS5 нужен пакет PySocks).

    Returns:
        dict[str, str] | None: ``{'http': url, 'https': url}`` если URL задан, иначе ``None``.
    """
    raw = (getattr(settings, "TELEGRAM_API_PROXY_URL", None) or "").strip()
    if not raw:
        return None
    return {"http": raw, "https": raw}
