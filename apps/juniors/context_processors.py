"""Публичная навигация детской лиги и действия авторизованного взрослого."""

from django.http import HttpRequest


def junior_context(request: HttpRequest) -> dict[str, bool]:
    """Показывает лигу всем; конкретные полномочия проверяют представления и сервисы."""
    return {
        "junior_actions_allowed": bool(
            request.user.is_authenticated and request.user.is_active
        ),
        "junior_league_visible": True,
    }
