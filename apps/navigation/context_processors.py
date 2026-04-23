"""
Context processors for navigation app.
"""

from django.core.cache import cache

from .models import MenuItem

NAV_MENU_CACHE_KEY = "nav_menu_items:v1"


def nav_menu_items(request):
    """Добавляет активные пункты меню в контекст шаблонов."""
    items = cache.get_or_set(
        NAV_MENU_CACHE_KEY,
        lambda: list(MenuItem.objects.filter(is_active=True).order_by("order", "id")),
        timeout=300,
    )
    return {"nav_menu_items": items}
