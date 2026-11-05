"""Главное меню использует только пункты админки без повторной ссылки на лигу."""

import re

import pytest
from django.core.cache import cache

from apps.navigation.models import MenuItem

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("active", [True, False])
def test_junior_menu_respects_admin_order_and_visibility(client, settings, active):
    """Позиция и видимость одинаковы в мобильном и десктопном меню без дубликатов."""
    settings.SECURE_SSL_REDIRECT = False
    MenuItem.objects.all().delete()
    MenuItem.objects.create(title="Турниры", url="/tournaments/", order=1)
    MenuItem.objects.create(
        title="Детская лига", url="/kids/", order=2, is_active=active
    )
    MenuItem.objects.create(title="Рейтинг", url="/ratings/", order=3)
    cache.clear()
    try:
        response = client.get("/kids/rules/")
        assert response.status_code == 200
        html = response.content.decode()
        desktop = re.search(r'id="nav-menu-track"[^>]*>(.*?)</div>', html, re.S).group(
            1
        )
        mobile = re.search(r'id="nav-menu-mobile"[^>]*>(.*?)</div>', html, re.S).group(
            1
        )
        for menu in (desktop, mobile):
            assert menu.count('href="/kids/"') == int(active)
            if active:
                assert (
                    menu.index("Турниры")
                    < menu.index("Детская лига")
                    < menu.index("Рейтинг")
                )
    finally:
        cache.clear()
