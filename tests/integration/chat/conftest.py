"""Изолированные настройки транспорта городских чатов."""

from collections.abc import Iterator

import pytest
from django.test import override_settings


@pytest.fixture(autouse=True)
def chat_settings() -> Iterator[None]:
    """Отключает внешние сервисы и включает локальный канал событий."""
    with override_settings(
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
        SECURE_SSL_REDIRECT=False,
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
        TELEGRAM_ENABLED=False,
    ):
        yield
