"""
Поддерживает бессрочный бесплатный тариф на каждом запросе авторизованного пользователя.
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.subscriptions.services import ensure_current_subscription


class FreeSubscriptionMiddleware:
    """Назначает Free новым пользователям и возвращает его после окончания платного тарифа."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            ensure_current_subscription(user)
        return self.get_response(request)
