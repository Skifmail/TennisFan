"""Отзыв устройства удаляет подписку и у провайдера, а не только в базе."""

import json
import uuid
from collections.abc import Iterator
from datetime import timedelta
from io import StringIO
from typing import Final

import pytest
import requests
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from pytest_mock import MockerFixture

from apps.notifications.device_binding import revoke_devices
from apps.notifications.domain.catalog import EVENTS
from apps.notifications.infrastructure.push import (
    PushRejectedError,
    delete_subscription,
)
from apps.notifications.models import (
    Delivery,
    EventPolicy,
    NotificationPreference,
    PushDevice,
)
from apps.notifications.worker import process_deliveries
from apps.users.models import User

pytestmark = pytest.mark.django_db
SUBSCRIPTION: Final = "00000000-0000-4000-8000-0000000000aa"
NEXT_SUBSCRIPTION: Final = "00000000-0000-4000-8000-0000000000bb"


@pytest.fixture(autouse=True)
def provider_settings() -> Iterator[None]:
    """Задаёт фиктивные ключи OneSignal, чтобы не ходить в настоящий API."""
    EventPolicy.objects.bulk_create(
        [
            EventPolicy(
                key=key, title=event.title, group=event.group, description=event.title
            )
            for key, event in EVENTS.items()
        ],
        ignore_conflicts=True,
    )
    with override_settings(
        NOTIFICATION_CENTER_ENABLED=True,
        SECURE_SSL_REDIRECT=False,
        ONESIGNAL_APP_ID="test-app-id",
        ONESIGNAL_REST_API_KEY="test-key",
        ALLOWED_HOSTS=["testserver"],
        ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru",
    ):
        yield


def _device(user: User, *, active: bool = True) -> PushDevice:
    """Создаёт устройство с известной подпиской."""
    return PushDevice.objects.create(
        user=user,
        device_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        subscription_id=SUBSCRIPTION,
        active=active,
        expires_at=timezone.now() + timedelta(days=7),
    )


def _response(status_code: int) -> object:
    """Возвращает минимальный ответ requests для проверки статуса."""
    return type("Response", (), {"status_code": status_code})()


def test_delete_subscription_when_accepted_should_delete_remotely(
    mocker: MockerFixture,
) -> None:
    """Принятое удаление подтверждается кодом 202 и точным адресом подписки."""
    delete = mocker.patch(
        "apps.notifications.infrastructure.push.requests.delete",
        return_value=_response(202),
    )
    assert delete_subscription(SUBSCRIPTION)
    url = delete.call_args.args[0]
    assert url.endswith(f"/apps/test-app-id/subscriptions/{SUBSCRIPTION}")
    assert delete.call_args.kwargs["headers"] == {"Authorization": "Key test-key"}


def test_delete_subscription_when_already_gone_should_count_as_success(
    mocker: MockerFixture,
) -> None:
    """404 означает, что цель достигнута: повтор не нужен."""
    mocker.patch(
        "apps.notifications.infrastructure.push.requests.delete",
        return_value=_response(404),
    )
    assert delete_subscription(SUBSCRIPTION)


def test_delete_subscription_when_provider_fails_should_not_raise(
    mocker: MockerFixture,
) -> None:
    """Отказ провайдера не ломает отзыв устройства в базе."""
    mocker.patch(
        "apps.notifications.infrastructure.push.requests.delete",
        return_value=_response(500),
    )
    assert not delete_subscription(SUBSCRIPTION)


def test_delete_subscription_when_network_fails_should_not_raise(
    mocker: MockerFixture,
) -> None:
    """Сетевой сбой отдаёт безопасный отказ без исключения."""
    mocker.patch(
        "apps.notifications.infrastructure.push.requests.delete",
        side_effect=requests.RequestException("boom"),
    )
    assert not delete_subscription(SUBSCRIPTION)


def test_delete_subscription_without_key_should_not_call_provider(
    mocker: MockerFixture,
) -> None:
    """Без REST-ключа команда не делает сетевых запросов."""
    delete = mocker.patch("apps.notifications.infrastructure.push.requests.delete")
    with override_settings(ONESIGNAL_REST_API_KEY=""):
        assert not delete_subscription(SUBSCRIPTION)
    delete.assert_not_called()


def test_revoke_devices_should_clear_local_row_and_subscription(
    mocker: MockerFixture,
) -> None:
    """Отзыв оставляет запись для аудита, но без адреса и активности."""
    user = User.objects.create_user(email="revoke@example.test")
    device = _device(user)
    delete = mocker.patch(
        "apps.notifications.device_binding.delete_subscription", return_value=True
    )
    assert revoke_devices([device]) == 1
    delete.assert_called_once_with(SUBSCRIPTION)
    device.refresh_from_db()
    assert not device.active
    assert device.subscription_id is None


def test_revoke_when_device_has_no_subscription_should_not_call_provider(
    mocker: MockerFixture,
) -> None:
    """Пустая запись отключается без обращения к провайдеру."""
    user = User.objects.create_user(email="empty@example.test")
    device = PushDevice.objects.create(
        user=user,
        device_hash="e" * 64,
        active=False,
        expires_at=timezone.now() + timedelta(days=1),
    )
    delete = mocker.patch("apps.notifications.device_binding.delete_subscription")
    assert revoke_devices([device]) == 1
    delete.assert_not_called()


def _bind(client: Client) -> dict[str, object]:
    """Создаёт поколение привязки для текущего браузера."""
    client.force_login(User.objects.create_user(email=f"{uuid.uuid4()}@example.test"))
    response = client.post(reverse("notification_identity"))
    assert response.status_code == 200
    return dict(response.json())


def _post_device(
    client: Client, identity: dict[str, object], *, active: bool, subscription: str
) -> object:
    """Отправляет состояние устройства так же, как это делает браузер."""
    return client.post(
        reverse("notification_device"),
        data=json.dumps(
            {
                "externalId": identity["externalId"],
                "active": active,
                "subscriptionId": subscription,
                "enableAccount": False,
            }
        ),
        content_type="application/json",
    )


def _bind(client: Client, user: User) -> dict[str, object]:
    """Создаёт строку устройства для текущего браузера и возвращает поколение."""
    client.force_login(user)
    response = client.post(reverse("notification_identity"))
    assert response.status_code == 200
    device = PushDevice.objects.get(user=user)
    device.active = True
    device.subscription_id = SUBSCRIPTION
    device.save(update_fields=["active", "subscription_id", "updated_at"])
    return dict(response.json())


def test_revoke_from_browser_should_delete_provider_subscription(
    client: Client, mocker: MockerFixture
) -> None:
    """Выключение уведомлений в браузере удаляет подписку у провайдера."""
    user = User.objects.create_user(email="browser-revoke@example.test")
    identity = _bind(client, user)
    device = PushDevice.objects.get(user=user)
    delete = mocker.patch(
        "apps.notifications.device_binding.delete_subscription", return_value=True
    )
    response = _post_device(
        client, identity, active=False, subscription=str(device.subscription_id)
    )
    assert response.status_code == 200
    delete.assert_called_once_with(SUBSCRIPTION)
    device.refresh_from_db()
    assert not device.active
    assert device.subscription_id is None


def test_resubscribe_should_delete_replaced_subscription(
    client: Client, mocker: MockerFixture
) -> None:
    """Смена подписки в том же браузере удаляет прежний адрес у провайдера."""
    user = User.objects.create_user(email="resubscribe@example.test")
    identity = _bind(client, user)
    device = PushDevice.objects.get(user=user)
    delete = mocker.patch(
        "apps.notifications.device_binding.delete_subscription", return_value=True
    )
    response = _post_device(
        client, identity, active=True, subscription=NEXT_SUBSCRIPTION
    )
    assert response.status_code == 200
    delete.assert_called_once_with(SUBSCRIPTION)
    device.refresh_from_db()
    assert not device.active


def test_retention_should_delete_provider_subscription(
    mocker: MockerFixture,
) -> None:
    """Ретенция снимает подписку у провайдера перед удалением записи."""
    user = User.objects.create_user(email="retention-revoke@example.test")
    old = timezone.now() - timedelta(days=40)
    _device(user)
    PushDevice.objects.filter(user=user).update(expires_at=old, updated_at=old)
    delete = mocker.patch(
        "apps.notifications.device_binding.delete_subscription", return_value=True
    )
    call_command("cleanup_notification_history", stdout=StringIO())
    delete.assert_called_once_with(SUBSCRIPTION)
    assert not PushDevice.objects.exists()


def test_retention_dry_run_should_not_touch_provider(
    mocker: MockerFixture,
) -> None:
    """Предпросмотр не удаляет подписки у провайдера."""
    user = User.objects.create_user(email="retention-dry@example.test")
    old = timezone.now() - timedelta(days=40)
    _device(user)
    PushDevice.objects.filter(user=user).update(expires_at=old, updated_at=old)
    delete = mocker.patch("apps.notifications.device_binding.delete_subscription")
    call_command("cleanup_notification_history", "--dry-run", stdout=StringIO())
    delete.assert_not_called()
    assert PushDevice.objects.exists()


def test_worker_when_no_recipients_should_delete_provider_subscription(
    mocker: MockerFixture,
) -> None:
    """Мёртвая подписка удаляется у провайдера, а не только отключается."""
    user = User.objects.create_user(email="worker-revoke@example.test")
    NotificationPreference.objects.create(user=user, push_enabled=True)
    _device(user)
    now = timezone.now()
    Delivery.objects.create(
        event_key="match.created",
        user=user,
        channel="push",
        title="Тема",
        body="Текст",
        url="https://tennisfan.ru/users/notifications/",
        available_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(hours=1),
    )
    mocker.patch(
        "apps.notifications.worker.send_push",
        side_effect=PushRejectedError("push_no_recipients"),
    )
    delete = mocker.patch(
        "apps.notifications.device_binding.delete_subscription", return_value=True
    )
    process_deliveries(limit=1)
    delete.assert_called_once_with(SUBSCRIPTION)
    assert not PushDevice.objects.get(user=user).active
