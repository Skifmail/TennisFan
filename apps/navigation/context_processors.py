"""
Context processors for navigation app.
"""

from .models import MenuItem


def nav_menu_items(request):
    """Добавляет активные пункты меню в контекст шаблонов."""
    items = MenuItem.objects.filter(is_active=True).order_by("order", "id")
    return {"nav_menu_items": list(items)}
