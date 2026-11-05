"""Открытая детская лига с сохранением авторизации и изоляции семей."""

import pytest
from django.urls import reverse

from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup(settings):
    settings.SECURE_SSL_REDIRECT = False


@pytest.mark.parametrize(
    "page", ["home", "rules", "ratings", "tournaments", "parent_join"]
)
def test_public_pages_and_launch_notice(client, page):
    response = client.get(reverse("juniors:" + page))
    assert response.status_code == 200
    assert "Детская лига запущена" in response.content.decode()
    assert "Детская лига находится в разработке" not in response.content.decode()


def test_guest_sees_parent_registration_and_private_pages_require_login(client):
    html = client.get(reverse("juniors:parent_join")).content.decode()
    assert 'name="email"' in html
    assert "Создать аккаунт" in html
    assert client.get(reverse("juniors:family")).status_code == 302
    assert client.post(reverse("juniors:add_child"), {}).status_code == 302


def test_regular_parent_can_open_add_child_and_has_no_admin_rights(client):
    parent = make_user(email="public-parent@kids.test", email_verified=True)
    client.force_login(parent)
    html = client.get(reverse("juniors:family")).content.decode()
    assert reverse("juniors:add_child") in html
    assert client.get(reverse("juniors:add_child")).status_code == 200
