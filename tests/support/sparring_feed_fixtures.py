"""Общий зритель и география для проверок ленты спаррингов."""

from collections.abc import Iterator

import pytest
from django.test import Client

from apps.core.models import City
from apps.users.models import Player
from tests.support.factories import make_player


@pytest.fixture
def viewer(client: Client) -> Iterator[Player]:
    """Создаёт зрителя и города с известным порядком расстояний.

    Args:
        client: Тестовый HTTP-клиент.

    Yields:
        Игрок, авторизованный в клиенте.
    """
    for name, lat, lng in [
        ("Точка старта", 55.5, 38.2),
        ("Ближний город", 55.6, 38.3),
        ("Дальний город", 43.5, 39.7),
    ]:
        City.objects.create(name=name, lat=lat, lng=lng)
    player = make_player(email_suffix="feed-viewer")
    Player.objects.filter(pk=player.pk).update(city="Точка старта")
    player.refresh_from_db()
    client.force_login(player.user)
    yield player
