"""Проверки актуальной роли автора и пакетной загрузки бейджей модераторов."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.chat import services
from apps.chat.models import ChatMembership, ChatMessage
from apps.core.models import City
from tests.support.chat_legal import join_city_with_consent
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("moderator", "staff", "active_member", "active_user", "expected"),
    [
        (False, False, True, True, False),
        (True, False, True, True, True),
        (False, True, True, True, True),
        (True, False, False, True, False),
        (True, False, True, False, False),
        (False, True, False, True, False),
        (False, True, True, False, False),
    ],
)
def test_badge_when_author_role_checked_should_match_active_room_permissions(
    moderator: bool,
    staff: bool,
    active_member: bool,
    active_user: bool,
    expected: bool,
) -> None:
    """Бейдж соответствует роли автора, а не правам читателя или старому назначению.

    Args:
        moderator: Назначена ли автору роль в этой комнате.
        staff: Имеет ли автор административные права платформы.
        active_member: Действует ли участие автора.
        active_user: Активен ли аккаунт автора.
        expected: Ожидаемая видимость бейджа.
    """
    viewer = make_user(email="badge-viewer@test.local")
    author = make_user(email="badge-author@test.local", is_staff=staff)
    room = join_city_with_consent(
        user=viewer, city_id=City.objects.create(name="Город бейджа").pk
    )
    join_city_with_consent(user=author, city_id=room.city_id)
    message = ChatMessage.objects.create(room=room, author=author, text="Привет")
    ChatMembership.objects.filter(room=room, user=author).update(
        is_moderator=moderator, is_active=active_member
    )
    author.is_active = active_user
    author.save(update_fields=["is_active"])
    payload = services.history(user=viewer, room_id=room.pk)["messages"][0]
    assert payload.get("author_is_moderator") is expected
    assert services.serialize_message(message).get("author_is_moderator") is expected


def test_badge_when_author_moderates_other_city_should_not_leak_role() -> None:
    """Назначение в одном городе не даёт бейдж в другом."""
    author = make_user(email="badge-local@test.local")
    rooms = [
        join_city_with_consent(
            user=author, city_id=City.objects.create(name=f"Город роли {index}").pk
        )
        for index in range(2)
    ]
    ChatMembership.objects.filter(user=author, room=rooms[0]).update(is_moderator=True)
    for room in rooms:
        ChatMessage.objects.create(room=room, author=author, text="Сообщение")
    first = services.history(user=author, room_id=rooms[0].pk)["messages"][0]
    second = services.history(user=author, room_id=rooms[1].pk)["messages"][0]
    assert first.get("author_is_moderator") is True
    assert second.get("author_is_moderator") is False


def test_badge_when_author_removed_should_not_mark_deleted_account() -> None:
    """Сообщения без автора не получают знак модератора."""
    user = make_user(email="badge-deleted@test.local")
    room = join_city_with_consent(
        user=user, city_id=City.objects.create(name="Город удалённого автора").pk
    )
    ChatMessage.objects.create(room=room, author=None, text="Старое сообщение")
    message = services.history(user=user, room_id=room.pk)["messages"][0]
    assert message.get("author_is_moderator") is False


def test_badge_when_role_changes_without_messages_should_refresh_existing_history() -> (
    None
):
    """Назначение и снятие роли обновляют старые сообщения без новых событий."""
    user = make_user(email="badge-refresh@test.local")
    room = join_city_with_consent(
        user=user, city_id=City.objects.create(name="Город смены роли").pk
    )
    message = services.send_message(
        user=user, room_id=room.pk, text="Старое сообщение", client_id=uuid4()
    )
    cursor = services.history(user=user, room_id=room.pk)["cursor"]
    for moderator in (True, False):
        ChatMembership.objects.filter(room=room, user=user).update(
            is_moderator=moderator
        )
        sync = services.sync_events(user=user, room_id=room.pk, after_id=cursor)
        assert sync["events"] == []
        assert sync.get("moderator_ids") == ([user.pk] if moderator else [])
        history = services.history(user=user, room_id=room.pk)["messages"][0]
        assert history["id"] == message.pk
        assert history.get("author_is_moderator") is moderator


def test_badge_when_many_messages_loaded_should_not_query_role_per_message() -> None:
    """История и журнал загружают роли одним запросом на всю страницу."""
    user = make_user(email="badge-batch@test.local")
    room = join_city_with_consent(
        user=user, city_id=City.objects.create(name="Город пакетной загрузки").pk
    )
    ChatMembership.objects.filter(room=room, user=user).update(is_moderator=True)
    first = services.send_message(
        user=user, room_id=room.pk, text="Первое", client_id=uuid4()
    )
    with CaptureQueriesContext(connection) as initial_history:
        services.history(user=user, room_id=room.pk)
    with CaptureQueriesContext(connection) as initial_sync:
        services.sync_events(user=user, room_id=room.pk, after_id=0)
    for index in range(10):
        author = make_user(email=f"badge-batch-{index}@test.local")
        join_city_with_consent(user=author, city_id=room.city_id)
        services.send_message(
            user=author, room_id=room.pk, text="Привет", client_id=uuid4()
        )
    with CaptureQueriesContext(connection) as expanded_history:
        history = services.history(user=user, room_id=room.pk)
    with CaptureQueriesContext(connection) as expanded_sync:
        sync = services.sync_events(user=user, room_id=room.pk, after_id=0)
    assert len(history["messages"]) == 11
    assert history["messages"][0]["id"] == first.pk
    assert history["messages"][0].get("author_is_moderator") is True
    assert sync["events"][0]["message"].get("author_is_moderator") is True
    assert len(initial_history) == len(expanded_history)
    assert len(initial_sync) == len(expanded_sync)
