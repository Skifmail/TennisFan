"""Восстановление переписки после пропущенных сигналов Redis."""

from uuid import uuid4

import pytest

from apps.chat import services
from apps.chat.models import ChatEvent, ChatMessage
from apps.core.models import City
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def test_recovery_when_old_message_edited_and_deleted_should_replay_changes() -> None:
    """Правки за пределами первой страницы не теряются при reconnect."""
    user = make_user(email="recover-chat@test.local")
    room = services.join_city(
        user=user, city_id=City.objects.get_or_create(name="Рязань")[0].pk
    )
    first = services.send_message(
        user=user, room_id=room.pk, text="Первое", client_id=uuid4()
    )
    cursor = services.history(user=user, room_id=room.pk)["cursor"]
    services.edit_message(
        user=user, room_id=room.pk, message_id=first.pk, text="Правка"
    )
    changed = services.sync_events(user=user, room_id=room.pk, after_id=cursor)
    revision = changed["events"][0]["message"]["revision"]
    assert revision > 0
    services.delete_message(user=user, room_id=room.pk, message_id=first.pk)
    latest = services.sync_events(
        user=user, room_id=room.pk, after_id=changed["cursor"]
    )
    assert latest["events"][0]["message"]["revision"] > revision
    assert latest["events"][0]["message"]["text"] == ""


def test_recovery_when_more_than_page_should_return_all_events_without_gaps() -> None:
    """Пагинация журнала не пропускает сообщения при массовом восстановлении."""
    user = make_user(email="many-events@test.local")
    room = services.join_city(
        user=user, city_id=City.objects.get_or_create(name="Псков")[0].pk
    )
    message = ChatMessage.objects.create(
        room=room, author=user, client_id=uuid4(), text="Сообщение"
    )
    ChatEvent.objects.bulk_create(
        [ChatEvent(room=room, message=message) for _ in range(105)]
    )
    first = services.sync_events(user=user, room_id=room.pk, after_id=0)
    second = services.sync_events(user=user, room_id=room.pk, after_id=first["cursor"])
    assert len(first["events"]) == 100 and first["has_more"]
    assert len(second["events"]) == 5 and not second["has_more"]
    assert first["events"][-1]["id"] < second["events"][0]["id"]
