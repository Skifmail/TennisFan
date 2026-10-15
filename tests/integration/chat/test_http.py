"""Проверки HTTP-интерфейса и защиты городского чата."""

import json
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse

from apps.chat import services
from apps.core.models import City
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def test_pages_when_logged_in_should_join_other_city_and_render(client: Client) -> None:
    """Каталог и комната доступны без платной подписки."""
    user = make_user(email="http-chat@test.local")
    client.force_login(user)
    city = City.objects.get_or_create(name="Омск", region="Омская область")[0]
    assert client.get(reverse("chat:index")).status_code == 200
    response = client.post(reverse("chat:join"), {"city_id": city.pk})
    assert response.status_code == 302
    page = client.get(response.headers["Location"])
    assert page.status_code == 200
    assert "Омск" in page.content.decode()
    assert "city-chat" in page.content.decode()
    assert city.pk in {
        row["id"]
        for row in client.get(reverse("chat:cities"), {"q": "Омск"}).json()["cities"]
    }


@pytest.mark.parametrize(
    "body",
    [
        "null",
        "[]",
        "{",
        '{"action":"send","text":123}',
        '{"action":"read","message_id":true}',
        '{"action":"send","text":"Текст","client_id":"не UUID"}',
    ],
)
def test_action_when_invalid_payload_should_return_400(
    client: Client, body: str
) -> None:
    """Повреждённые данные не приводят к внутренним ошибкам."""
    user = make_user(email="bad-http@test.local")
    client.force_login(user)
    room = services.join_city(
        user=user, city_id=City.objects.get_or_create(name="Тверь")[0].pk
    )
    response = client.post(
        reverse("chat:action", args=[room.pk]),
        data=body,
        content_type="application/json",
    )
    assert response.status_code == 400


def test_actions_when_authorized_should_send_sync_and_reject_outsider(
    client: Client,
) -> None:
    """Сообщения и изменения изолированы участием в комнате."""
    user = make_user(email="send-http@test.local", first_name="Ира")
    room = services.join_city(
        user=user, city_id=City.objects.get_or_create(name="Тула")[0].pk
    )
    client.force_login(user)
    action_url = reverse("chat:action", args=[room.pk])
    response = client.post(
        action_url,
        data=json.dumps(
            {"action": "send", "text": "<b>Привет</b>", "client_id": str(uuid4())}
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    payload = response.json()["message"]
    assert payload["text"] == "<b>Привет</b>"
    assert "@" not in payload["author"]
    assert client.get(reverse("chat:sync", args=[room.pk]), {"after": 0}).json()[
        "events"
    ]
    assert (
        client.get(reverse("chat:history", args=[room.pk]), {"before": "x"}).status_code
        == 400
    )
    client.force_login(make_user(email="outsider-http@test.local"))
    assert client.get(reverse("chat:history", args=[room.pk])).status_code == 403
    assert (
        client.post(
            action_url,
            data=json.dumps(
                {"action": "send", "text": "Чужой", "client_id": str(uuid4())}
            ),
            content_type="application/json",
        ).status_code
        == 403
    )


def test_action_when_no_csrf_should_reject(client: Client) -> None:
    """Авторизация не отключает защиту отправки от CSRF."""
    user = make_user(email="csrf-chat@test.local")
    secured = Client(enforce_csrf_checks=True)
    secured.force_login(user)
    room = services.join_city(
        user=user, city_id=City.objects.get_or_create(name="Пермь")[0].pk
    )
    assert (
        secured.post(
            reverse("chat:action", args=[room.pk]),
            data='{"action":"leave"}',
            content_type="application/json",
        ).status_code
        == 403
    )


def test_api_when_guest_should_not_expose_history(client: Client) -> None:
    """Гость получает отказ, а не данные комнаты."""
    assert client.get(reverse("chat:history", args=[1])).status_code == 403
    assert client.get(reverse("chat:index")).status_code == 302


def test_room_when_identifier_exceeds_database_range_should_reject_without_500(
    client: Client,
) -> None:
    """Большой числовой сегмент URL не вызывает переполнение драйвера БД."""
    user = make_user(email="large-id-chat@test.local")
    client.force_login(user)
    response = client.get(f"/chats/api/rooms/{10**30}/history/")
    assert response.status_code == 404


def test_index_when_profile_city_should_suggest_it_without_restricting_membership(
    client: Client,
) -> None:
    """Город профиля используется как стартовый поисковый запрос, а не запрет."""
    from apps.users.models import Player

    user = make_user(email="profile-city-chat@test.local")
    Player.objects.create(user=user, city="г. Казань")
    client.force_login(user)
    response = client.get(reverse("chat:index"))
    assert response.context["query"] == "Казань"
    assert client.get(reverse("chat:index"), {"q": "Омск"}).context["query"] == "Омск"
