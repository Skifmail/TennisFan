"""Группировка разделов исходящих писем в сайдбаре админки.

Выносит proxy-модели ``OutboundEmail`` из приложения «Ядро» в отдельный
сворачиваемый блок «Электронные письма».
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.contrib import admin
from django.http import HttpRequest

_EMAIL_OBJECT_NAMES: frozenset[str] = frozenset(
    {
        "OutboundEmail",
        "RegistrationOutboundEmail",
        "NewTournamentOutboundEmail",
        "TournamentOutboundEmail",
        "SubscriptionOutboundEmail",
        "SecurityOutboundEmail",
        "ClubsOutboundEmail",
        "SupportOutboundEmail",
        "OtherOutboundEmail",
    }
)

# Порядок пунктов внутри «Электронные письма».
_EMAIL_ORDER: dict[str, int] = {
    "OutboundEmail": 0,
    "NewTournamentOutboundEmail": 1,
    "RegistrationOutboundEmail": 2,
    "TournamentOutboundEmail": 3,
    "SubscriptionOutboundEmail": 4,
    "SecurityOutboundEmail": 5,
    "ClubsOutboundEmail": 6,
    "SupportOutboundEmail": 7,
    "OtherOutboundEmail": 8,
}


def _is_email_model(model: dict[str, Any]) -> bool:
    """Проверить, относится ли пункт меню к журналу писем."""
    return str(model.get("object_name") or "") in _EMAIL_OBJECT_NAMES


def patch_outbound_email_admin_menu() -> None:
    """Переопределить ``admin.site.get_app_list`` для группировки писем."""
    original_get_app_list: Callable[..., list[dict[str, Any]]] = admin.site.get_app_list

    def get_app_list(
        request: HttpRequest,
        app_label: str | None = None,
    ) -> list[dict[str, Any]]:
        """Вернуть список приложений с блоком «Электронные письма».

        Args:
            request (HttpRequest): Запрос администратора.
            app_label (str | None): Фильтр по одному приложению.

        Returns:
            list[dict[str, Any]]: Список приложений для сайдбара/индекса.
        """
        app_list = original_get_app_list(request, app_label)
        if app_label is not None and app_label not in {"core", "outbound_emails"}:
            return app_list

        email_models: list[dict[str, Any]] = []
        for app in app_list:
            if app.get("app_label") != "core":
                continue
            kept: list[dict[str, Any]] = []
            for model in app.get("models") or []:
                if _is_email_model(model):
                    email_models.append(model)
                else:
                    kept.append(model)
            app["models"] = kept

        # Убрать пустое «Ядро», если вдруг всё уехало (не ожидается).
        app_list = [
            app
            for app in app_list
            if app.get("models") or app.get("app_label") != "core"
        ]

        if not email_models:
            return app_list

        email_models.sort(
            key=lambda model: (
                _EMAIL_ORDER.get(str(model.get("object_name") or ""), 99),
                str(model.get("name") or ""),
            )
        )
        first_url = str(email_models[0].get("admin_url") or "")
        email_app: dict[str, Any] = {
            "name": "Электронные письма",
            "app_label": "outbound_emails",
            "app_url": first_url,
            "has_module_perms": True,
            "models": email_models,
        }

        # Вставить блок сразу после «Ядро», иначе в начало списка.
        insert_at = 0
        for index, app in enumerate(app_list):
            if app.get("app_label") == "core":
                insert_at = index + 1
                break
        app_list.insert(insert_at, email_app)
        return app_list

    admin.site.get_app_list = get_app_list
