"""Сценарии участия, отправки и модерации городского чата."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone
from pytest_mock import MockerFixture

from apps.chat import services
from apps.chat.domain import ChatAccessError, ChatRateLimitError, ChatValidationError
from apps.chat.models import ChatMembership, ChatMessage, ChatReport, ChatRoom
from apps.core.models import City
from apps.users.models import Player, User
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def user() -> User:
    """Создаёт игрока из Москвы."""
    account = make_user(email="chat@test.local", first_name="Анна")
    Player.objects.create(user=account, city="Москва")
    return account


@pytest.fixture
def room(user: User) -> ChatRoom:
    """Вступает в комнату другого города."""
    city = City.objects.get_or_create(name="Казань")[0]
    return services.join_city(user=user, city_id=city.pk)


def test_join_city_when_other_city_should_allow_and_be_idempotent(
    user: User, room: ChatRoom
) -> None:
    """Город профиля не ограничивает участие, повторное вступление безопасно."""
    assert services.join_city(user=user, city_id=room.city_id).pk == room.pk
    assert (
        ChatMembership.objects.filter(room=room, user=user, is_active=True).count() == 1
    )


def test_send_when_retry_should_create_one_message_and_one_event(
    user: User, room: ChatRoom, mocker: MockerFixture
) -> None:
    """Повтор запроса не создаёт дубль и не нарушает ограничение частоты."""
    publish = mocker.patch("apps.chat.services.publish_room_change")
    key = uuid4()
    with transaction.atomic():
        first = services.send_message(
            user=user, room_id=room.pk, text="Привет", client_id=key
        )
        retry = services.send_message(
            user=user, room_id=room.pk, text="Привет", client_id=key
        )
        assert not publish.called
    assert first.pk == retry.pk
    assert room.messages.count() == 1
    assert room.events.count() == 1


def test_send_when_not_member_or_left_should_deny(user: User, room: ChatRoom) -> None:
    """Выход прекращает право читать и отправлять сообщения."""
    services.leave_room(user=user, room_id=room.pk)
    with pytest.raises(ChatAccessError):
        services.send_message(
            user=user, room_id=room.pk, text="Привет", client_id=uuid4()
        )
    with pytest.raises(ChatAccessError):
        services.history(user=user, room_id=room.pk)


def test_send_when_fast_or_cross_room_reply_should_reject(
    user: User, room: ChatRoom
) -> None:
    """Антиспам и изоляция ответов работают на сервере."""
    first = services.send_message(
        user=user, room_id=room.pk, text="Привет", client_id=uuid4()
    )
    with pytest.raises(ChatRateLimitError):
        services.send_message(user=user, room_id=room.pk, text="Ещё", client_id=uuid4())
    other = services.join_city(
        user=user, city_id=City.objects.get_or_create(name="Омск")[0].pk
    )
    with pytest.raises(ChatValidationError):
        services.send_message(
            user=user,
            room_id=other.pk,
            text="Ответ",
            client_id=uuid4(),
            reply_to_id=first.pk,
        )


def test_history_when_paging_and_read_should_not_lose_or_mark_future(
    user: User, room: ChatRoom
) -> None:
    """История пагинируется, отметка прочтения не движется назад."""
    author = make_user(email="other-chat@test.local", first_name="Иван")
    services.join_city(user=author, city_id=room.city_id)
    messages = [
        ChatMessage.objects.create(
            room=room, author=author, text=str(index), client_id=uuid4()
        )
        for index in range(55)
    ]
    page = services.history(user=user, room_id=room.pk)
    assert len(page["messages"]) == 50
    assert page["has_more"]
    older = services.history(
        user=user, room_id=room.pk, before_id=page["messages"][0]["id"]
    )
    assert len(older["messages"]) == 5
    services.mark_read(user=user, room_id=room.pk, message_id=messages[-1].pk)
    services.mark_read(user=user, room_id=room.pk, message_id=messages[0].pk)
    assert (
        ChatMembership.objects.get(room=room, user=user).last_read_message_id
        == messages[-1].pk
    )
    with pytest.raises(ChatValidationError):
        services.mark_read(user=user, room_id=room.pk, message_id=messages[-1].pk + 1)


def test_moderation_when_author_or_staff_should_enforce_permissions(
    user: User, room: ChatRoom
) -> None:
    """Редактировать может автор в срок, модерировать — только модератор."""
    message = services.send_message(
        user=user, room_id=room.pk, text="Первый", client_id=uuid4()
    )
    other = make_user(email="mod-chat@test.local")
    services.join_city(user=other, city_id=room.city_id)
    with pytest.raises(ChatAccessError):
        services.edit_message(
            user=other, room_id=room.pk, message_id=message.pk, text="Подмена"
        )
    services.edit_message(
        user=user, room_id=room.pk, message_id=message.pk, text="Исправлено"
    )
    assert (
        services.sync_events(user=user, room_id=room.pk, after_id=0)["events"][-1][
            "message"
        ]["text"]
        == "Исправлено"
    )
    ChatMessage.objects.filter(pk=message.pk).update(
        created_at=timezone.now() - timedelta(hours=1)
    )
    with pytest.raises(ChatAccessError):
        services.edit_message(
            user=user, room_id=room.pk, message_id=message.pk, text="Поздно"
        )
    other.is_staff = True
    other.save(update_fields=["is_staff"])
    services.delete_message(user=other, room_id=room.pk, message_id=message.pk)
    payload = services.history(user=user, room_id=room.pk)["messages"][0]
    assert payload["is_deleted"] and payload["text"] == ""


def test_report_and_ban_when_moderated_should_preserve_ban_on_rejoin(
    user: User, room: ChatRoom
) -> None:
    """Жалоба идемпотентна, выход и повторное вступление не обходят бан."""
    message = services.send_message(
        user=user, room_id=room.pk, text="Сообщение", client_id=uuid4()
    )
    staff = make_user(email="staff-chat@test.local", is_staff=True)
    services.join_city(user=staff, city_id=room.city_id)
    services.report_message(
        user=staff, room_id=room.pk, message_id=message.pk, reason="Спам"
    )
    services.report_message(
        user=staff, room_id=room.pk, message_id=message.pk, reason="Спам"
    )
    assert ChatReport.objects.count() == 1
    with pytest.raises(ChatAccessError):
        services.moderate_member(
            user=user, room_id=room.pk, target_id=staff.pk, banned=True
        )
    services.moderate_member(
        user=staff, room_id=room.pk, target_id=user.pk, banned=True
    )
    services.leave_room(user=user, room_id=room.pk)
    services.join_city(user=user, city_id=room.city_id)
    with pytest.raises(ChatAccessError):
        services.send_message(
            user=user, room_id=room.pk, text="Обход", client_id=uuid4()
        )


def test_membership_when_muted_should_not_receive_reply_notification(
    user: User, room: ChatRoom
) -> None:
    """Уведомления об ответах учитывают настройку участника."""
    first = services.send_message(
        user=user, room_id=room.pk, text="Вопрос", client_id=uuid4()
    )
    services.set_notifications(user=user, room_id=room.pk, enabled=False)
    other = make_user(email="reply-chat@test.local")
    services.join_city(user=other, city_id=room.city_id)
    services.send_message(
        user=other,
        room_id=room.pk,
        text="Ответ",
        client_id=uuid4(),
        reply_to_id=first.pk,
    )
    assert not user.notifications.exists()


@pytest.mark.django_db(transaction=True)
def test_leave_when_not_member_should_not_broadcast(
    user: User, room: ChatRoom, mocker: MockerFixture
) -> None:
    """Посторонний аккаунт и повторный выход не вызывают fan-out комнаты."""
    outsider = make_user(email="leave-outsider@test.local")
    publish = mocker.patch("apps.chat.services.publish_room_change")
    services.leave_room(user=outsider, room_id=room.pk)
    publish.assert_not_called()
    services.leave_room(user=user, room_id=room.pk)
    assert publish.call_count == 1
    services.leave_room(user=user, room_id=room.pk)
    assert publish.call_count == 1
