"""Стартовый каталог показывает населённые сообщества, не подменяя поиск."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.chat.models import ChatMembership, ChatRoom
from apps.core.models import City
from apps.users.models import Player
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def test_index_when_opened_without_query_should_sort_populated_chats_by_size(
    client: Client,
) -> None:
    """Стартовый список исключает пустые и закрытые комнаты и сортирует по численности.

    Args:
        client: Клиент игрока с указанным в профиле городом.
    """
    viewer = make_user(email="discovery-viewer@test.local")
    Player.objects.create(user=viewer, city="Город профиля")
    member = make_user(email="discovery-member@test.local")
    disabled = make_user(email="discovery-disabled@test.local", is_active=False)
    large = ChatRoom.objects.create(city=City.objects.create(name="Ярославль"))
    small = ChatRoom.objects.create(city=City.objects.create(name="Астрахань"))
    equal = ChatRoom.objects.create(city=City.objects.create(name="Брянск"))
    closed = ChatRoom.objects.create(
        city=City.objects.create(name="Закрытый город"), is_active=False
    )
    draft = ChatRoom.objects.create(
        city=City.objects.create(name="Заготовка"), is_created=False
    )
    departed = ChatRoom.objects.create(city=City.objects.create(name="Вышедшие"))
    blocked = ChatRoom.objects.create(city=City.objects.create(name="Отключённые"))
    ChatRoom.objects.create(city=City.objects.create(name="Пустой"))
    City.objects.create(name="Без комнаты")
    for room in (large, small, equal, closed, draft):
        ChatMembership.objects.create(room=room, user=member)
    ChatMembership.objects.create(room=large, user=viewer)
    ChatMembership.objects.create(room=departed, user=member, is_active=False)
    ChatMembership.objects.create(room=blocked, user=disabled)
    client.force_login(viewer)

    response = client.get(reverse("chat:index"))

    assert response.status_code == 200
    assert response.context["query"] == ""
    assert [city.pk for city in response.context["cities"]] == [
        large.city_id,
        small.city_id,
        equal.city_id,
    ]
    assert [
        getattr(city, "member_count", None) for city in response.context["cities"]
    ] == [
        2,
        1,
        1,
    ]


def test_index_when_searching_or_requesting_all_cities_should_keep_catalog_search(
    client: Client,
) -> None:
    """Явный поиск и ссылка всех городов по-прежнему позволяют найти новый город.

    Args:
        client: Авторизованный HTTP-клиент.
    """
    city = City.objects.create(name="Новый город для поиска")
    client.force_login(make_user(email="discovery-search@test.local"))

    response = client.get(reverse("chat:index"), {"q": city.name})
    assert [item.pk for item in response.context["cities"]] == [city.pk]
    assert "Создать чат" in response.content.decode()
    all_cities = client.get(reverse("chat:index"), {"q": ""})
    assert city.pk in {item.pk for item in all_cities.context["cities"]}


def test_index_when_no_populated_chat_should_keep_search_available(
    client: Client,
) -> None:
    """Пустой стартовый список не мешает найти город и создать первое сообщество.

    Args:
        client: Авторизованный HTTP-клиент.
    """
    City.objects.create(name="Город без сообщества")
    client.force_login(make_user(email="discovery-empty@test.local"))
    response = client.get(reverse("chat:index"))
    assert response.context["cities"] == []
    assert 'id="chat-city-search"' in response.content.decode()
