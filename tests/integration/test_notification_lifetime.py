"""Продление привязки устройства при авторизованной активности."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.notifications.models import PushDevice
from apps.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def lifetime_settings() -> Iterator[None]:
    """Включает центр и разрешает локальный тестовый домен."""
    cache.clear()
    with override_settings(
        NOTIFICATION_CENTER_ENABLED=True,
        SECURE_SSL_REDIRECT=False,
        ONESIGNAL_REST_API_KEY="test-not-a-key",
        ALLOWED_HOSTS=["testserver"],
        ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru",
    ):
        yield
    cache.clear()


def _bind(client: Client) -> PushDevice:
    user = User.objects.create_user(email="lifetime@example.test")
    client.force_login(user)
    response = client.post(reverse("notification_identity"))
    assert response.status_code == 200
    return PushDevice.objects.get(user=user)


def test_activity_when_authenticated_should_extend_device(client: Client) -> None:
    """Любая авторизованная страница продлевает привязку, а не только настройки."""
    device = _bind(client)
    PushDevice.objects.filter(pk=device.pk).update(
        expires_at=timezone.now() + timedelta(days=1)
    )
    assert client.get(reverse("home")).status_code == 200
    device.refresh_from_db()
    assert device.expires_at > timezone.now() + timedelta(days=13)


def test_activity_when_extended_recently_should_not_write_every_request(
    client: Client,
) -> None:
    """Троттлинг не делает UPDATE на каждый запрос."""
    device = _bind(client)
    client.get(reverse("home"))
    device.refresh_from_db()
    deadline = device.expires_at
    PushDevice.objects.filter(pk=device.pk).update(
        expires_at=deadline - timedelta(minutes=30)
    )
    assert client.get(reverse("home")).status_code == 200
    device.refresh_from_db()
    assert device.expires_at == deadline - timedelta(minutes=30)


def test_activity_when_another_user_logs_in_should_not_extend_old_device(
    client: Client,
) -> None:
    """Чужой аккаунт не продлевает привязку предыдущего владельца."""
    device = _bind(client)
    old_expiry = timezone.now() + timedelta(days=1)
    PushDevice.objects.filter(pk=device.pk).update(expires_at=old_expiry)
    other = User.objects.create_user(email="other-lifetime@example.test")
    client.force_login(other)
    assert client.get(reverse("home")).status_code == 200
    device.refresh_from_db()
    assert device.expires_at == old_expiry


def test_activity_without_cookie_should_not_create_device(client: Client) -> None:
    """Обычный просмотр не создаёт привязку без явного подключения."""
    user = User.objects.create_user(email="no-cookie@example.test")
    client.force_login(user)
    assert client.get(reverse("home")).status_code == 200
    assert not PushDevice.objects.exists()
