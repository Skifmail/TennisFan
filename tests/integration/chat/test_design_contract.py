"""Контракт изолированного светлого интерфейса городского чата."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.models import City
from tests.support.chat_legal import join_city_with_consent
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def test_room_when_member_should_render_light_shell_and_joined_cities(
    client: Client,
) -> None:
    """Комната использует свой интерфейс и даёт переход в другие свои города."""
    user = make_user(email="chat-design@test.local", first_name="Анна")
    first = join_city_with_consent(
        user=user, city_id=City.objects.get_or_create(name="Казань")[0].pk
    )
    second = join_city_with_consent(
        user=user, city_id=City.objects.get_or_create(name="Самара")[0].pk
    )
    client.force_login(user)
    response = client.get(reverse("chat:room", args=[first.pk]))
    html = response.content.decode()
    assert response.status_code == 200
    assert {member.room_id for member in response.context["memberships"]} == {
        first.pk,
        second.pk,
    }
    assert 'aria-label="Мои городские чаты"' in html
    assert 'id="chat-settings"' in html
    assert 'id="chat-action-dialog"' in html
    assert "css/style.min.css" not in html


def test_directory_when_opened_should_share_light_shell(client: Client) -> None:
    """Каталог и комната принадлежат одному интерфейсу, без старой темы."""
    client.force_login(make_user(email="chat-directory-design@test.local"))
    response = client.get(reverse("chat:index"))
    html = response.content.decode()
    assert 'class="chat-app' in html
    assert 'id="chat-city-search"' in html
    assert "css/style.min.css" not in html
