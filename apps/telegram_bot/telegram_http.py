"""
Общие параметры HTTP для запросов к Telegram Bot API.

Используется при необходимости прокси (например, если ``api.telegram.org`` недоступен напрямую).
"""

from django.conf import settings


def is_telegram_api_enabled() -> bool:
    """
    Разрешены ли исходящие HTTP-вызовы к Telegram Bot API в этом процессе.

    При значении ``False`` (переменная окружения ``TELEGRAM_ENABLED``) приложение
    не обращается к ``api.telegram.org``, чтобы избежать таймаутов при блокировках.

    Returns:
        bool: ``True``, если вызовы к API разрешены; иначе ``False``.
    """
    return bool(getattr(settings, "TELEGRAM_ENABLED", True))


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
