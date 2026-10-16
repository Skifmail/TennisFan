"""Проверки численности городских чатов без дополнительных запросов на карточку."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.chat import services
from apps.chat.models import ChatMembership, ChatMessage, ChatRoom
from apps.core.models import City
from tests.support.chat_legal import join_city_with_consent
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("has_room", [False, True])
def test_directory_when_chat_empty_should_show_zero_participants(
    client: Client, has_room: bool
) -> None:
    """Пустой чат отображает ноль, а ещё не созданный город предлагает создание.

    Args:
        client: Тестовый HTTP-клиент.
        has_room: Создана ли комната для города.
    """
    user = make_user(email="counts-empty@test.local")
    city = City.objects.create(name="Город без участников", region="Тест")
    if has_room:
        ChatRoom.objects.create(city=city)
    client.force_login(user)
    response = client.get(reverse("chat:index"), {"q": city.name})
    assert response.status_code == 200
    assert getattr(response.context["cities"][0], "member_count", None) == 0
    assert ("0 участников" if has_room else "Создать чат") in response.content.decode()
    assert not ChatMembership.objects.filter(user=user).exists()


def test_counts_when_members_leave_or_accounts_disabled_should_count_only_readers(
    client: Client,
) -> None:
    """Численность одинакова в каталоге, списке своих городов и шапке комнаты.

    Args:
        client: Тестовый HTTP-клиент.
    """
    viewer = make_user(email="counts-viewer@test.local")
    city = City.objects.create(name="Город читателей", region="Тестовая область")
    room = join_city_with_consent(user=viewer, city_id=city.pk)
    muted = make_user(email="counts-muted@test.local")
    join_city_with_consent(user=muted, city_id=city.pk)
    ChatMembership.objects.filter(user=muted, room=room).update(
        banned_until=timezone.now() + timedelta(days=1)
    )
    departed = make_user(email="counts-departed@test.local")
    join_city_with_consent(user=departed, city_id=city.pk)
    services.leave_room(user=departed, room_id=room.pk)
    disabled = make_user(email="counts-disabled@test.local")
    join_city_with_consent(user=disabled, city_id=city.pk)
    disabled.is_active = False
    disabled.save(update_fields=["is_active"])
    client.force_login(viewer)
    response = client.get(reverse("chat:index"), {"q": city.name})
    assert getattr(response.context["cities"][0], "member_count", None) == 2
    assert getattr(response.context["memberships"][0], "member_count", None) == 2
    html = response.content.decode()
    assert html.count("2 участника") == 3  # Каталог, боковой и мобильный списки.
    assert city.region in html
    page = client.get(reverse("chat:room", args=[room.pk]))
    assert page.context["member_count"] == 2
    services.leave_room(user=muted, room_id=room.pk)
    response = client.get(reverse("chat:index"), {"q": city.name})
    assert getattr(response.context["cities"][0], "member_count", None) == 1
    assert "1 участник" in response.content.decode()


def test_directory_when_room_closed_should_not_advertise_its_members(
    client: Client,
) -> None:
    """Участники закрытой комнаты не попадают в численность доступного сообщества.

    Args:
        client: Тестовый HTTP-клиент.
    """
    user = make_user(email="counts-closed@test.local")
    city = City.objects.create(name="Закрытый чат")
    room = join_city_with_consent(user=user, city_id=city.pk)
    room.is_active = False
    room.save(update_fields=["is_active"])
    client.force_login(user)
    response = client.get(reverse("chat:index"), {"q": city.name})
    assert getattr(response.context["cities"][0], "member_count", None) == 0
    assert not response.context["memberships"]


def test_memberships_when_messages_and_readers_present_should_not_multiply_counts() -> (
    None
):
    """Подсчёт участников не искажает непрочитанное и не создаёт N+1 запросов."""
    viewer = make_user(email="counts-unread@test.local")
    author = make_user(email="counts-author@test.local")
    third = make_user(email="counts-third@test.local")
    for index in range(3):
        room = join_city_with_consent(
            user=viewer, city_id=City.objects.create(name=f"Город счётчика {index}").pk
        )
        join_city_with_consent(user=author, city_id=room.city_id)
        join_city_with_consent(user=third, city_id=room.city_id)
        for user in (viewer, author, author):
            ChatMessage.objects.create(
                room=room, author=user, text="Сообщение", client_id=uuid4()
            )
    with CaptureQueriesContext(connection) as queries:
        rows = list(services.my_memberships(user=viewer))
        assert len(rows) == 3
        for member in rows:
            assert getattr(member, "member_count", None) == 3
            assert member.unread == 2
    assert len(queries) == 1


def test_directory_when_more_cards_added_should_keep_query_count_constant(
    client: Client,
) -> None:
    """Число запросов каталога не растёт вместе с числом карточек городов.

    Args:
        client: Тестовый HTTP-клиент.
    """
    user = make_user(email="counts-queries@test.local")
    client.force_login(user)
    join_city_with_consent(
        user=user, city_id=City.objects.create(name="Масштаб счётчика 0").pk
    )
    # Разовый прогрев общих контекстных процессоров не относится к карточкам чата.
    client.get(reverse("chat:index"), {"q": "Масштаб счётчика"})
    with CaptureQueriesContext(connection) as initial:
        response = client.get(reverse("chat:index"), {"q": "Масштаб счётчика"})
        assert response.status_code == 200
    for index in range(1, 6):
        join_city_with_consent(
            user=user, city_id=City.objects.create(name=f"Масштаб счётчика {index}").pk
        )
    with CaptureQueriesContext(connection) as expanded:
        response = client.get(reverse("chat:index"), {"q": "Масштаб счётчика"})
        assert len(response.context["cities"]) == 6
        assert response.content.decode().count("1 участник") == 18
    assert len(expanded) == len(initial)
