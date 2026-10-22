"""Проверки списка участников городского чата и приватности профилей."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.chat.anonymous import ANONYMOUS_EMOJIS
from apps.chat.models import ChatMembership
from apps.core.models import City
from apps.users.models import Player, User
from tests.support.chat_legal import join_city_with_consent
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def _join_room(*, user: User, city_id: int) -> None:
    """Добавляет пользователя в комнату с действующими согласиями."""
    join_city_with_consent(user=user, city_id=city_id)


def test_members_when_profiles_hidden_should_not_disclose_private_fields(
    client: Client,
) -> None:
    """Скрытые имя и фото заменяются анонимным значком, а не утекают в HTML."""
    viewer = make_user(
        email="members-viewer@test.local",
        first_name="Вера",
        last_name="Открытая",
    )
    city = City.objects.create(name="Город списка участников", region="Тест")
    room = join_city_with_consent(user=viewer, city_id=city.pk)

    hidden = make_user(
        email="members-hidden@test.local",
        first_name="Секрет",
        last_name="Скрытый",
    )
    _join_room(user=hidden, city_id=city.pk)
    Player.objects.create(user=hidden)
    Player.objects.filter(user=hidden).update(avatar="avatars/hidden-secret.jpg")
    ChatMembership.objects.filter(room=room, user=hidden).update(
        disclosure_name=False, disclosure_avatar=False
    )

    photo_only = make_user(
        email="members-photo@test.local",
        first_name="Только",
        last_name="Фото",
    )
    _join_room(user=photo_only, city_id=city.pk)
    Player.objects.create(user=photo_only)
    Player.objects.filter(user=photo_only).update(avatar="avatars/visible-photo.jpg")
    ChatMembership.objects.filter(room=room, user=photo_only).update(
        disclosure_name=False, disclosure_avatar=True
    )

    client.force_login(viewer)
    response = client.get(reverse("chat:members", args=[room.pk]))

    assert response.status_code == 200
    payload = response.json()
    html = payload["html"]
    assert payload["total"] == 3
    assert payload["has_more"] is False
    assert "Вера Открытая" in html
    assert "Секрет" not in html
    assert "Скрытый" not in html
    assert "avatars/hidden-secret.jpg" not in html
    assert "avatars/visible-photo.jpg" in html
    assert html.count("Аноним") == 2
    assert any(emoji in html for emoji in ANONYMOUS_EMOJIS)
    assert client.get(reverse("chat:members", args=[room.pk])).json()["html"] == html


def test_members_when_outsider_should_reject_without_room_data(client: Client) -> None:
    """Посторонний не получает ни список, ни имена участников."""
    member = make_user(email="members-insider@test.local")
    room = join_city_with_consent(
        user=member,
        city_id=City.objects.create(name="Закрытый список").pk,
    )
    client.force_login(make_user(email="members-outsider@test.local"))

    response = client.get(reverse("chat:members", args=[room.pk]))

    assert response.status_code == 403
    assert "error" in response.json()
    assert "members-insider" not in response.content.decode()


def test_members_when_page_is_full_should_return_next_page(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Следующая страница отдаёт остаток списка и снимает признак продолжения."""
    monkeypatch.setattr("apps.chat.views.PARTICIPANTS_PAGE_SIZE", 2)
    viewer = make_user(email="members-page-viewer@test.local")
    room = join_city_with_consent(
        user=viewer,
        city_id=City.objects.create(name="Город страниц участников").pk,
    )
    _join_room(
        user=make_user(email="members-page-second@test.local"),
        city_id=room.city_id,
    )
    _join_room(
        user=make_user(email="members-page-third@test.local"),
        city_id=room.city_id,
    )
    client.force_login(viewer)

    first = client.get(reverse("chat:members", args=[room.pk])).json()
    second = client.get(reverse("chat:members", args=[room.pk]), {"page": 2}).json()

    assert first["total"] == 3
    assert first["page"] == 1
    assert first["has_more"] is True
    assert first["html"].count('class="chat-member"') == 2
    assert second["page"] == 2
    assert second["has_more"] is False
    assert second["html"].count('class="chat-member"') == 1


def test_room_when_member_should_render_participant_list_trigger(
    client: Client,
) -> None:
    """Счётчик в шапке становится кнопкой, открывающей диалог участников."""
    user = make_user(email="members-trigger@test.local")
    room = join_city_with_consent(
        user=user,
        city_id=City.objects.create(name="Город кнопки участников").pk,
    )
    client.force_login(user)

    response = client.get(reverse("chat:room", args=[room.pk]))
    html = response.content.decode()

    assert response.status_code == 200
    assert "data-open-chat-members" in html
    assert 'aria-controls="chat-members"' in html
    assert 'id="chat-members"' in html
    assert "Участники: 1" in html
