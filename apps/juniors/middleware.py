"""Режим просмотра детской лиги до завершения разработки."""

from django.http import HttpRequest
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin


class JuniorDevelopmentMiddleware(MiddlewareMixin):
    """Закрывает действия на сервере, сохраняя просмотр и уведомления шлюза."""

    def process_view(self, request: HttpRequest, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if match is None or match.namespace != "juniors":
            return None
        if match.url_name == "payment_webhook":
            # Уже начатые платежи должны получить подтверждение от шлюза.
            return None
        if (
            request.user.is_authenticated
            and request.user.is_active
            and request.user.is_staff
        ):
            return None
        read_pages = {
            "home",
            "rules",
            "ratings",
            "tournaments",
            "tournament",
            "family",
            "child",
            "adult_history",
            "payments",
        }
        if (
            request.method in {"GET", "HEAD", "OPTIONS"}
            and match.url_name in read_pages
        ):
            return None
        response = render(request, "juniors/development.html", status=403)
        response["Cache-Control"] = "no-store"
        return response
