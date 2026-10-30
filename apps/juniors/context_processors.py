"""Доступность детской лиги в общей навигации."""

from django.conf import settings
from django.http import HttpRequest


def junior_context(request: HttpRequest) -> dict[str, bool]:
    return {
        "junior_league_visible": settings.JUNIOR_LEAGUE_ENABLED
        or bool(request.user.is_authenticated and request.user.is_staff)
    }
