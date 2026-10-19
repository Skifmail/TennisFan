"""Контракт защищённой привязки браузера и изоляции аккаунтов."""

import json
from collections.abc import Iterator

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.test import Client, override_settings
from django.urls import reverse
from pytest_mock import MockerFixture

from apps.notifications.models import PushDevice
from apps.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def private_key() -> str:
    """Генерирует временный P-256 ключ, не имеющий отношения к OneSignal."""
    return (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


@pytest.fixture(autouse=True)
def identity_settings(private_key: str) -> Iterator[None]:
    """Включает локальный JWT без внешних запросов."""
    with override_settings(
        NOTIFICATION_CENTER_ENABLED=True,
        SECURE_SSL_REDIRECT=False,
        ONESIGNAL_IDENTITY_PRIVATE_KEY=private_key,
        ALLOWED_HOSTS=["testserver"],
    ):
        yield


def test_identity_when_anonymous_should_reject(client: Client) -> None:
    """Аноним не получает JWT привязки к какому-либо аккаунту."""
    assert client.post(reverse("notification_identity")).status_code == 302
    assert not PushDevice.objects.exists()


def test_identity_when_logged_in_should_sign_only_opaque_server_alias(
    client: Client, private_key: str
) -> None:
    """Сервер не принимает external_id и user_id из пользовательских данных."""
    user = User.objects.create_user(email="owner@example.test")
    client.force_login(user)
    response = client.post(
        reverse("notification_identity"), {"user_id": "999", "external_id": "victim"}
    )
    assert response.status_code == 200
    payload = response.json()
    key = serialization.load_pem_private_key(private_key.encode(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    public_key = key.public_key()
    claims = jwt.decode(
        payload["jwt"], public_key, algorithms=["ES256"], options={"verify_aud": False}
    )
    assert claims["identity"]["external_id"] == payload["externalId"]
    assert "owner@example.test" not in json.dumps(claims)
    device = PushDevice.objects.get()
    assert device.user_id == user.pk
    assert str(device.external_id) == payload["externalId"]
    assert not device.active
    assert "no-store" in response["Cache-Control"]


def test_identity_when_account_changes_should_rotate_and_revoke(client: Client) -> None:
    """Другой аккаунт в том же браузере не наследует адрес предыдущего."""
    first = User.objects.create_user(email="first@example.test")
    second = User.objects.create_user(email="second@example.test")
    client.force_login(first)
    old = client.post(reverse("notification_identity")).json()["externalId"]
    client.post(
        reverse("notification_device"),
        data=json.dumps(
            {"active": True, "externalId": str(PushDevice.objects.get().external_id)}
        ),
        content_type="application/json",
    )
    client.force_login(second)
    new = client.post(reverse("notification_identity")).json()["externalId"]
    assert old != new
    device = PushDevice.objects.get()
    assert device.user_id == second.pk
    assert not device.active


def test_logout_when_device_bound_should_revoke_before_sdk_callback(
    client: Client,
) -> None:
    """Персональные отправки прекращаются, даже если SDK logout недоступен."""
    user = User.objects.create_user(email="owner@example.test")
    client.force_login(user)
    client.post(reverse("notification_identity"))
    client.post(
        reverse("notification_device"),
        data=json.dumps(
            {"active": True, "externalId": str(PushDevice.objects.get().external_id)}
        ),
        content_type="application/json",
    )
    client.post(reverse("logout"))
    assert not PushDevice.objects.get().active


def test_push_transport_when_called_should_target_aliases_only(
    mocker: MockerFixture,
) -> None:
    """Ни segments, ни вся аудитория не могут попасть в персональный API-запрос."""
    from apps.notifications.infrastructure.push import send_push

    response = mocker.Mock(status_code=200)
    response.json.return_value = {"id": "accepted-id"}
    post = mocker.patch(
        "apps.notifications.infrastructure.push.requests.post", return_value=response
    )
    with override_settings(ONESIGNAL_REST_API_KEY="test-not-a-key"):
        result = send_push(
            aliases=["test-alias"],
            title="Тема",
            body="Текст",
            url="https://tennisfan.ru/",
            key="00000000-0000-4000-8000-000000000001",
        )
    assert result == "accepted-id"
    payload = post.call_args.kwargs["json"]
    assert payload["include_aliases"] == {"external_id": ["test-alias"]}
    assert "included_segments" not in payload
    assert payload["idempotency_key"] == "00000000-0000-4000-8000-000000000001"


def test_push_transport_when_response_has_no_id_should_not_report_success(
    mocker: MockerFixture,
) -> None:
    """HTTP 200 без ID не считается принятой доставкой."""
    from apps.notifications.infrastructure.push import PushRejectedError, send_push

    response = mocker.Mock(status_code=200)
    response.json.return_value = {"id": "", "errors": ["unsubscribed"]}
    mocker.patch(
        "apps.notifications.infrastructure.push.requests.post", return_value=response
    )
    with (
        override_settings(ONESIGNAL_REST_API_KEY="test-not-a-key"),
        pytest.raises(PushRejectedError),
    ):
        send_push(
            aliases=["test"],
            title="Тема",
            body="Текст",
            url="https://tennisfan.ru/",
            key="00000000-0000-4000-8000-000000000001",
        )


def test_device_when_old_tab_acknowledges_should_not_activate_new_account(
    client: Client,
) -> None:
    """Отложенный ответ старой вкладки не активирует новую привязку другого аккаунта."""
    first = User.objects.create_user(email="old-tab@example.test")
    second = User.objects.create_user(email="new-tab@example.test")
    client.force_login(first)
    old_alias = client.post(reverse("notification_identity")).json()["externalId"]
    client.force_login(second)
    client.post(reverse("notification_identity"))
    response = client.post(
        reverse("notification_device"),
        data=json.dumps({"active": True, "externalId": old_alias}),
        content_type="application/json",
    )
    assert response.status_code == 409
    assert not PushDevice.objects.get().active


def test_logout_when_old_tab_acknowledges_should_reject_revoked_alias(
    client: Client,
) -> None:
    """Отложенный запрос предыдущей сессии не оживляет отозванную привязку."""
    user = User.objects.create_user(email="logout-race@example.test")
    client.force_login(user)
    old_alias = client.post(reverse("notification_identity")).json()["externalId"]
    client.post(reverse("logout"))
    client.force_login(user)
    response = client.post(
        reverse("notification_device"),
        data=json.dumps({"active": True, "externalId": old_alias}),
        content_type="application/json",
    )
    assert response.status_code == 409
    assert not PushDevice.objects.get().active


@pytest.mark.django_db(transaction=True)
def test_device_when_identity_rotates_concurrently_should_not_activate_new_owner(
    client: Client,
) -> None:
    """Проверка владельца и активация не разделены небезопасным окном гонки."""
    import uuid
    from collections.abc import Callable
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from typing import Any

    from django.db import connection, connections, transaction

    if connection.vendor != "postgresql":
        pytest.skip("Проверка настоящей блокировки строки требует PostgreSQL")
    first = User.objects.create_user(email="concurrent-old@example.test")
    second = User.objects.create_user(email="concurrent-new@example.test")
    client.force_login(first)
    old_alias = client.post(reverse("notification_identity")).json()["externalId"]
    reached_write_or_lock = Event()

    def _execute(
        execute: Callable[..., Any],
        sql: str,
        params: Any,
        many: bool,
        context: dict[str, Any],
    ) -> Any:
        if "notifications_pushdevice" in sql and (
            sql.lstrip().startswith("UPDATE") or "FOR UPDATE" in sql
        ):
            reached_write_or_lock.set()
        return execute(sql, params, many, context)

    def _acknowledge() -> int:
        try:
            with connection.execute_wrapper(_execute):
                return client.post(
                    reverse("notification_device"),
                    data=json.dumps({"active": True, "externalId": old_alias}),
                    content_type="application/json",
                ).status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with transaction.atomic():
            device = PushDevice.objects.select_for_update().get()
            device.user = second
            device.external_id = uuid.uuid4()
            device.active = False
            device.save()
            result = executor.submit(_acknowledge)
            assert reached_write_or_lock.wait(timeout=10), "Запрос не дошёл до привязки"
        assert result.result(timeout=10) == 409
    device.refresh_from_db()
    assert device.user_id == second.pk
    assert not device.active
