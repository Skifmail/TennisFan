"""Приёмка тестового push только на подтверждённое устройство пользователя."""

import json
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Final, cast
from urllib.parse import urlsplit

import pytest
from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from pytest_mock import MockerFixture

from apps.notifications.infrastructure.push import (
    PushRejectedError,
    PushTransportError,
)
from apps.notifications.models import PushDevice
from apps.users.models import User

pytestmark = pytest.mark.django_db
SUBSCRIPTION: Final = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def test_push_settings() -> Iterator[None]:
    """Изолирует транспорт, кэш и домены тестовой доставки.

    Yields:
        Настройки локального центра без реальных запросов к OneSignal.
    """
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


def _user_with_device(
    *,
    active: bool = True,
    expired: bool = False,
    subscription: str = SUBSCRIPTION,
) -> User:
    """Создаёт пользователя и подтверждённое устройство для проверки.

    Args:
        active: Подписка разрешена пользователем.
        expired: Срок привязки уже истёк.
        subscription: UUID подписки OneSignal.

    Returns:
        Владелец устройства.
    """
    user = User.objects.create_user(email=f"{uuid.uuid4()}@example.test")
    PushDevice.objects.create(
        user=user,
        device_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        active=active,
        subscription_id=uuid.UUID(subscription),
        expires_at=timezone.now()
        + (timedelta(minutes=-1) if expired else timedelta(days=1)),
    )
    return user


def _post(client: Client, subscription: str = SUBSCRIPTION) -> HttpResponse:
    """Отправляет запрос тестовой доставки с CSRF-совместимым JSON.

    Args:
        client: Авторизованный клиент.
        subscription: UUID текущей подписки.

    Returns:
        Ответ защищённого маршрута.
    """
    return cast(
        HttpResponse,
        client.post(
            reverse("notification_test_push"),
            data=json.dumps({"subscriptionId": subscription}),
            content_type="application/json",
        ),
    )


def test_test_push_when_anonymous_should_redirect_to_login(client: Client) -> None:
    """Без сессии тестовый push не отправляется и не раскрывает устройство."""
    assert _post(client).status_code == 302


def test_test_push_when_device_confirmed_should_send_only_that_subscription(
    client: Client, mocker: MockerFixture
) -> None:
    """Тест уходит на одну подписку и содержит безопасную ссылку в настройки."""
    user = _user_with_device()
    client.force_login(user)
    send = mocker.patch("apps.notifications.views.send_push", return_value="message")
    response = _post(client)
    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    kwargs = send.call_args.kwargs
    assert kwargs["subscription_ids"] == [SUBSCRIPTION]
    assert uuid.UUID(kwargs["key"])
    target = urlsplit(kwargs["url"])
    assert target.scheme == "https"
    assert target.netloc == "tennisfan.ru"
    assert target.path == reverse("notifications")
    assert "subscription" not in kwargs["url"].lower()


@pytest.mark.parametrize(
    ("active", "expired"),
    [
        (False, False),
        (True, True),
    ],
)
def test_test_push_when_device_not_available_should_not_call_provider(
    client: Client,
    mocker: MockerFixture,
    active: bool,
    expired: bool,
) -> None:
    """Отозванная или истёкшая привязка не превращается в тестовую рассылку."""
    user = _user_with_device(active=active, expired=expired)
    client.force_login(user)
    send = mocker.patch("apps.notifications.views.send_push")
    response = _post(client)
    assert response.status_code == 409
    assert response.json() == {"error": "device_not_found"}
    send.assert_not_called()


def test_test_push_when_device_belongs_to_another_user_should_reject(
    client: Client, mocker: MockerFixture
) -> None:
    """ID чужой подписки нельзя использовать для проверки доставки."""
    _user_with_device()
    client.force_login(User.objects.create_user(email="other@example.test"))
    send = mocker.patch("apps.notifications.views.send_push")
    assert _post(client).status_code == 409
    send.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [b"{}", b'{"subscriptionId": "not-a-uuid"}', b"[]", b"not-json"],
)
def test_test_push_when_payload_invalid_should_reject(
    client: Client, mocker: MockerFixture, body: bytes
) -> None:
    """Некорректный UUID не вызывает провайдера и не даёт перебрать адреса."""
    user = _user_with_device()
    client.force_login(user)
    send = mocker.patch("apps.notifications.views.send_push")
    response = client.post(
        reverse("notification_test_push"),
        data=body,
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_subscription"}
    send.assert_not_called()


def test_test_push_when_repeated_should_rate_limit(
    client: Client, mocker: MockerFixture
) -> None:
    """Повтор в течение окна не создаёт второе сообщение."""
    client.force_login(_user_with_device())
    send = mocker.patch("apps.notifications.views.send_push", return_value="message")
    assert _post(client).status_code == 202
    response = _post(client)
    assert response.status_code == 429
    assert response.json() == {"error": "test_rate_limited"}
    assert response["Retry-After"] == "30"
    assert send.call_count == 1


def test_test_push_when_provider_has_no_recipients_should_deactivate_device(
    client: Client, mocker: MockerFixture
) -> None:
    """Мёртвая подписка отключается сразу после отказа провайдера."""
    client.force_login(_user_with_device())
    mocker.patch(
        "apps.notifications.views.send_push",
        side_effect=PushRejectedError("push_no_recipients"),
    )
    response = _post(client)
    assert response.status_code == 409
    assert response.json() == {"error": "device_unreachable"}
    assert not PushDevice.objects.get().active


@pytest.mark.parametrize(
    "error",
    [
        PushTransportError("push_network_error"),
        PushRejectedError("push_http_400"),
    ],
)
def test_test_push_when_provider_fails_should_return_safe_unavailable(
    client: Client, mocker: MockerFixture, error: Exception
) -> None:
    """Временная ошибка не раскрывает ответ провайдера и разрешает повтор."""
    client.force_login(_user_with_device())
    mocker.patch("apps.notifications.views.send_push", side_effect=error)
    response = _post(client)
    assert response.status_code == 503
    assert response.json() == {"error": "test_unavailable"}
    assert PushDevice.objects.get().active


def test_test_push_when_csrf_missing_should_reject(
    mocker: MockerFixture,
) -> None:
    """Тестовая отправка требует CSRF независимо от активной сессии."""
    user = _user_with_device()
    protected = Client(enforce_csrf_checks=True)
    protected.force_login(user)
    send = mocker.patch("apps.notifications.views.send_push")
    response = protected.post(
        reverse("notification_test_push"),
        data=json.dumps({"subscriptionId": SUBSCRIPTION}),
        content_type="application/json",
    )
    assert response.status_code == 403
    send.assert_not_called()
