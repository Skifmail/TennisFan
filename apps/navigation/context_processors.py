"""
Context processors for navigation app.
"""

from typing import cast
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.http import HttpRequest

from .models import MenuItem

NAV_MENU_CACHE_KEY = "nav_menu_items:v1"


def _is_menu_item_active(url: str, request: HttpRequest) -> bool:
    """Определяет, соответствует ли пункт меню текущему адресу страницы.

    Args:
        url: Адрес пункта меню (может содержать query-параметры).
        request: Текущий HTTP-запрос.

    Returns:
        bool: ``True``, если пункт меню ведёт на открытую страницу.
    """
    parts = urlsplit(url)
    target_path = parts.path or "/"

    if request.path == target_path:
        if not parts.query:
            return True
        params = parse_qs(parts.query)
        return all(request.GET.get(key) == values[0] for key, values in params.items())

    if parts.query:
        return False

    return bool(request.path.startswith(target_path.rstrip("/") + "/"))


def nav_menu_items(request: HttpRequest) -> dict[str, list[dict[str, str | bool]]]:
    """Добавляет активные пункты меню в контекст шаблонов.

    Args:
        request: Текущий HTTP-запрос.

    Returns:
        dict: Список пунктов меню с флагом активности для текущей страницы.
    """
    items = cast(
        list[MenuItem],
        cache.get_or_set(
            NAV_MENU_CACHE_KEY,
            lambda: list(
                MenuItem.objects.filter(is_active=True).order_by("order", "id")
            ),
            timeout=300,
        ),
    )
    return {
        "nav_menu_items": [
            {
                "title": item.title,
                "url": item.url,
                "is_external": item.is_external,
                "is_active": not item.is_external
                and _is_menu_item_active(item.url, request),
            }
            for item in items
        ]
    }
