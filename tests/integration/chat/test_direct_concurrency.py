"""Одновременное открытие личного диалога на настоящем PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection

from apps.chat.direct import open_direct
from apps.chat.models import ChatMembership, ChatRoom
from apps.users.models import User
from tests.support.factories import make_user

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Нужен PostgreSQL"),
]


def test_direct_when_both_players_start_concurrently_should_create_one_dialog() -> None:
    """Уникальность пары защищает оба порядка одновременного запроса."""
    first = make_user(email="direct-race-first@test.local")
    second = make_user(email="direct-race-second@test.local")
    barrier = Barrier(2)

    def submit(pair: tuple[User, User]) -> int:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return int(open_direct(user=pair[0], recipient_id=pair[1].pk).pk)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(submit, [(first, second), (second, first)]))
    assert ids[0] == ids[1]
    assert ChatRoom.objects.count() == 1
    assert ChatMembership.objects.count() == 2
