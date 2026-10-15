"""Проверки конкурентных записей на PostgreSQL, а не эмуляции SQLite."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection
from pytest_mock import MockerFixture

from apps.chat import services
from apps.chat.domain import ChatRateLimitError
from apps.core.models import City
from tests.support.chat_legal import join_city_with_consent
from tests.support.factories import make_user

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql", reason="Нужны блокировки PostgreSQL"
    ),
]


def test_send_when_concurrent_retry_should_commit_once() -> None:
    """Два одновременных запроса одного UUID сохраняют единственный результат."""
    user = make_user(email="race-chat@test.local")
    room = join_city_with_consent(
        user=user, city_id=City.objects.get_or_create(name="Тула")[0].pk
    )
    key = uuid4()
    barrier = Barrier(2)

    def submit() -> int:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return int(
                services.send_message(
                    user=user, room_id=room.pk, text="Один раз", client_id=key
                ).pk
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: submit(), range(2)))
    assert result[0] == result[1]
    assert room.messages.count() == room.events.count() == 1


def test_send_when_concurrent_different_cities_should_limit_account() -> None:
    """Переключение между комнатами не обходит антиспам одного аккаунта."""
    user = make_user(email="race-two-chat@test.local")
    rooms = [
        join_city_with_consent(
            user=user, city_id=City.objects.get_or_create(name=name)[0].pk
        )
        for name in ("Казань", "Омск")
    ]
    barrier = Barrier(2)

    def submit(room_id: int) -> str:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            services.send_message(
                user=user, room_id=room_id, text="Проверка", client_id=uuid4()
            )
            return "sent"
        except ChatRateLimitError:
            return "limited"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(submit, [room.pk for room in rooms]))
    assert sorted(result) == ["limited", "sent"]


def test_send_when_cross_replies_concurrent_should_not_deadlock(
    mocker: MockerFixture,
) -> None:
    """Уведомления двум авторам не образуют взаимную блокировку FK пользователей."""
    from typing import Any

    from apps.chat.models import ChatMessage
    from apps.users.models import Notification

    authors = [make_user(email=f"cross-reply-{index}@test.local") for index in range(2)]
    rooms = [
        join_city_with_consent(
            user=authors[0], city_id=City.objects.get_or_create(name=name)[0].pk
        )
        for name in ("Казань", "Омск")
    ]
    for room in rooms:
        join_city_with_consent(user=authors[1], city_id=room.city_id)
    originals = [
        ChatMessage.objects.create(
            room=room, author=authors[1 - index], text="Вопрос", client_id=uuid4()
        )
        for index, room in enumerate(rooms)
    ]
    from datetime import timedelta

    from django.utils import timezone

    ChatMessage.objects.filter(pk__in=[message.pk for message in originals]).update(
        created_at=timezone.now() - timedelta(seconds=10)
    )
    barrier = Barrier(2)
    create_notification = Notification.objects.create

    def notification(**kwargs: Any) -> Notification:
        barrier.wait(timeout=10)
        return create_notification(**kwargs)

    mocker.patch(
        "apps.chat.services.Notification.objects.create", side_effect=notification
    )

    def submit(index: int) -> int:
        close_old_connections()
        try:
            return int(
                services.send_message(
                    user=authors[index],
                    room_id=rooms[index].pk,
                    text="Ответ",
                    client_id=uuid4(),
                    reply_to_id=originals[index].pk,
                ).pk
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(list(pool.map(submit, range(2)))) == 2
    assert Notification.objects.count() == 2
