"""
Утилиты безопасных редиректов и передачи адреса возврата (``next``).
"""

from urllib.parse import urlencode

from django.http import HttpRequest
from django.utils.http import url_has_allowed_host_and_scheme


def get_safe_next_url(request: HttpRequest, fallback: str = "") -> str:
    """Извлечь проверенный адрес возврата из параметра ``next``.

    Значение берётся сначала из POST, затем из GET. Адрес принимается только
    если он ведёт на текущий хост, чтобы исключить открытый редирект.

    Args:
        request (HttpRequest): Текущий HTTP-запрос.
        fallback (str): Адрес, возвращаемый при отсутствующем или небезопасном ``next``.

    Returns:
        str: Безопасный адрес возврата либо ``fallback``.
    """
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback


def append_next(url: str, next_url: str) -> str:
    """Добавить параметр ``next`` к адресу, сохранив уже имеющиеся параметры.

    Args:
        url (str): Адрес, к которому добавляется параметр.
        next_url (str): Адрес возврата. Пустое значение оставляет ``url`` без изменений.

    Returns:
        str: Адрес с параметром ``next``.
    """
    if not next_url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode({'next': next_url})}"
