"""Состояние внешних способов входа для настроек аккаунта."""

from __future__ import annotations

from typing import TypedDict

from django.http import Http404

from .models import SocialIdentity, User
from .social_oauth import get_provider


class ConnectionProvider(TypedDict):
    """Доступность и состояние независимой привязки аккаунта."""

    key: str
    label: str
    linked: bool
    available: bool


def connection_providers(user: User) -> list[ConnectionProvider]:
    """Вернуть настроенные сервисы и сохранённые, но временно недоступные привязки."""
    linked = set(
        SocialIdentity.objects.filter(user=user).values_list("provider", flat=True)
    )
    providers: list[ConnectionProvider] = []
    for key in ("yandex", "vk"):
        try:
            config = get_provider(key)
        except Http404:
            if key in linked:
                providers.append(
                    {
                        "key": key,
                        "label": str(SocialIdentity.Provider(key).label),
                        "linked": True,
                        "available": False,
                    }
                )
        else:
            providers.append(
                {
                    "key": key,
                    "label": config.label,
                    "linked": key in linked,
                    "available": True,
                }
            )
    return providers
