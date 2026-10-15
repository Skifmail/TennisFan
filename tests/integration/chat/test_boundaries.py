"""Граничные случаи доступа и пользовательских настроек."""

from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

from apps.chat import services
from apps.chat.domain import ChatAccessError, ChatNotFoundError
from apps.chat.models import ChatRoom
from apps.core.models import City
from tests.support.chat_legal import join_city_with_consent
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def test_join_when_homonymous_cities_should_not_merge_rooms() -> None:
    """Одноимённые населённые пункты разных регионов не смешивают историю."""
    user = make_user(email="names-chat@test.local")
    a = City.objects.create(name="Тестовый", region="Первый регион")
    b = City.objects.create(name="Тестовый", region="Второй регион")
    first = join_city_with_consent(user=user, city_id=a.pk)
    second = join_city_with_consent(user=user, city_id=b.pk)
    assert first.pk != second.pk
    with pytest.raises(ChatNotFoundError):
        join_city_with_consent(user=user, city_id=99999999)
    ChatRoom.objects.filter(pk=first.pk).update(is_active=False)
    with pytest.raises(ChatNotFoundError):
        join_city_with_consent(user=user, city_id=a.pk)
    user.is_active = False
    with pytest.raises(ChatAccessError):
        join_city_with_consent(user=user, city_id=b.pk)


def test_actions_when_member_should_edit_report_read_delete_and_leave(
    client: Client,
) -> None:
    """HTTP-команды проходят общий сервисный путь и настройки действуют."""
    user = make_user(email="all-actions@test.local", is_staff=True)
    room = join_city_with_consent(
        user=user, city_id=City.objects.get_or_create(name="Владимир")[0].pk
    )
    message = services.send_message(
        user=user, room_id=room.pk, text="Сообщение", client_id=uuid4()
    )
    client.force_login(user)
    url = reverse("chat:action", args=[room.pk])
    for payload in [
        {"action": "edit", "message_id": message.pk, "text": "Исправлено"},
        {"action": "report", "message_id": message.pk, "reason": "Проверка"},
        {"action": "read", "message_id": message.pk},
        {"action": "notifications", "enabled": False},
        {"action": "delete", "message_id": message.pk},
        {"action": "leave"},
    ]:
        assert (
            client.post(url, payload, content_type="application/json").status_code
            == 200
        )
    assert client.get(reverse("chat:room", args=[room.pk])).status_code == 302


def test_unread_when_own_deleted_and_other_messages_should_count_only_other() -> None:
    """Непрочитанные не включают свои и удалённые сообщения."""
    user = make_user(email="unread-a@test.local")
    other = make_user(email="unread-b@test.local")
    room = join_city_with_consent(
        user=user, city_id=City.objects.get_or_create(name="Клин")[0].pk
    )
    join_city_with_consent(user=other, city_id=room.city_id)
    first = services.send_message(
        user=other, room_id=room.pk, text="Вопрос", client_id=uuid4()
    )
    services.send_message(
        user=user,
        room_id=room.pk,
        text="Ответ",
        reply_to_id=first.pk,
        client_id=uuid4(),
    )
    assert other.notifications.count() == 1
    assert list(services.my_memberships(user=user))[0].unread == 1
    services.delete_message(user=other, room_id=room.pk, message_id=first.pk)
    assert list(services.my_memberships(user=user))[0].unread == 0


def test_city_search_when_lowercase_cyrillic_should_find_city(client: Client) -> None:
    """Поиск населённых пунктов работает с кириллицей в SQLite и PostgreSQL."""
    user = make_user(email="search-chat@test.local")
    city = City.objects.get_or_create(name="Санкт-Петербург")[0]
    client.force_login(user)
    assert city.pk in {
        item["id"]
        for item in client.get(reverse("chat:cities"), {"q": "петербург"}).json()[
            "cities"
        ]
    }


def test_ban_when_staff_should_enforce_and_allow_explicit_unban(client: Client) -> None:
    """Команда модератора запрещает писать и поддерживает снятие ограничения."""
    from apps.chat.domain import ChatAccessError

    staff = make_user(email="http-staff-ban@test.local", is_staff=True)
    target = make_user(email="http-target-ban@test.local")
    room = join_city_with_consent(
        user=staff, city_id=City.objects.get_or_create(name="Брянск")[0].pk
    )
    join_city_with_consent(user=target, city_id=room.city_id)
    client.force_login(staff)
    url = reverse("chat:action", args=[room.pk])
    assert (
        client.post(
            url,
            {"action": "ban", "user_id": target.pk, "banned": True},
            content_type="application/json",
        ).status_code
        == 200
    )
    with pytest.raises(ChatAccessError):
        services.send_message(
            user=target, room_id=room.pk, text="Ограничено", client_id=uuid4()
        )
    assert (
        client.post(
            url,
            {"action": "ban", "user_id": target.pk, "banned": False},
            content_type="application/json",
        ).status_code
        == 200
    )
    assert (
        services.send_message(
            user=target, room_id=room.pk, text="Разрешено", client_id=uuid4()
        ).text
        == "Разрешено"
    )
