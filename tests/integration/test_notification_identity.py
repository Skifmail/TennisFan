"""Приёмка подтверждения подписки доставленным одноразовым кодом."""

import json
import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Final, cast
from urllib.parse import parse_qs, urlsplit

import pytest
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from pytest_mock import MockerFixture

from apps.notifications.models import EventPolicy, NotificationPreference, PushDevice
from apps.users.models import User

pytestmark = pytest.mark.django_db
SUBSCRIPTION: Final = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def identity_settings() -> Iterator[None]:
    """Изолирует транспорт и включает только локальный центр уведомлений."""
    with override_settings(
        NOTIFICATION_CENTER_ENABLED=True,
        SECURE_SSL_REDIRECT=False,
        ONESIGNAL_REST_API_KEY="test-not-a-key",
        ALLOWED_HOSTS=["testserver"],
        ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru",
    ):
        yield


def _bind(client: Client) -> dict[str, object]:
    user = User.objects.create_user(email=f"{uuid.uuid4()}@example.test")
    client.force_login(user)
    response = client.post(reverse("notification_identity"))
    assert response.status_code == 200
    return dict(response.json())


def _device(
    client: Client, identity: dict[str, object], **extra: object
) -> HttpResponse:
    return cast(
        HttpResponse,
        client.post(
            reverse("notification_device"),
            data=json.dumps(
                {
                    "externalId": identity["externalId"],
                    "active": True,
                    "subscriptionId": SUBSCRIPTION,
                    **extra,
                }
            ),
            content_type="application/json",
        ),
    )


def _start(client: Client, mocker: MockerFixture) -> tuple[dict[str, object], str]:
    identity = _bind(client)
    send = mocker.patch("apps.notifications.views.send_push", return_value="message")
    response = _device(client, identity, enableAccount=True)
    assert response.status_code == 202
    assert not PushDevice.objects.get().active
    assert not NotificationPreference.objects.filter(push_enabled=True).exists()
    url = send.call_args.kwargs["url"]
    assert not urlsplit(url).query
    return identity, parse_qs(urlsplit(url).fragment)["code"][0]


def _confirm(client: Client, code: str) -> HttpResponse:
    return cast(
        HttpResponse,
        client.post(reverse("notification_confirm"), {"code": code, "confirm": "yes"}),
    )


def test_identity_when_logged_in_should_not_require_jwt(client: Client) -> None:
    """Web SDK получает только поколение привязки, а не неподдерживаемый JWT."""
    identity = _bind(client)
    assert "jwt" not in identity
    assert identity["verifiedSubscriptionId"] is None


def test_identity_when_anonymous_should_reject(client: Client) -> None:
    """Аноним не получает привязку к аккаунту."""
    assert client.post(reverse("notification_identity")).status_code == 302


def test_confirmation_when_delivered_should_activate_only_after_post(
    client: Client, mocker: MockerFixture
) -> None:
    """Просмотр ссылки ничего не меняет, явное подтверждение активирует подписку."""
    _, code = _start(client, mocker)
    response = client.get(reverse("notification_confirm"))
    assert response.status_code == 200
    assert response["Referrer-Policy"] == "same-origin"
    assert "no-store" in response["Cache-Control"]
    assert not PushDevice.objects.get().active
    assert _confirm(client, code).status_code == 200
    device = PushDevice.objects.get()
    assert device.active
    assert str(device.subscription_id) == SUBSCRIPTION
    assert NotificationPreference.objects.get(user=device.user).push_enabled
    assert _confirm(client, code).status_code == 409


@pytest.mark.parametrize(
    "condition",
    ["wrong_code", "other_browser", "account_change", "logout", "expired", "opt_out"],
)
def test_confirmation_when_invalidated_should_not_activate(
    client: Client, mocker: MockerFixture, condition: str
) -> None:
    """Чужой браузер, смена владельца, отказ и истечение срока отзывают код."""
    identity, code = _start(client, mocker)
    if condition == "wrong_code":
        code = "not-the-delivered-secret"
    elif condition == "other_browser":
        other = Client()
        other.force_login(PushDevice.objects.get().user)
        client = other
    elif condition == "account_change":
        user = User.objects.create_user(email="another@example.test")
        client.force_login(user)
    elif condition == "logout":
        user = PushDevice.objects.get().user
        client.logout()
        client.force_login(user)
    elif condition == "expired":
        from apps.notifications.models import PushConfirmation

        PushConfirmation.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    else:
        assert _device(client, identity, active=False).status_code == 200
    assert _confirm(client, code).status_code == 409
    assert not PushDevice.objects.get().active


def test_device_when_pending_should_not_reveal_code_or_send_twice(
    client: Client, mocker: MockerFixture
) -> None:
    """Повторы не отправляют push автоматически и не раскрывают секрет в JSON."""
    identity = _bind(client)
    send = mocker.patch("apps.notifications.views.send_push", return_value="message")
    # Фоновая синхронизация не инициирует подтверждение.
    response = _device(client, identity)
    assert response.status_code == 200
    send.assert_not_called()
    response = _device(client, identity, enableAccount=True)
    assert response.status_code == 202
    assert "code" not in json.loads(response.content)
    assert _device(client, identity, enableAccount=True).status_code == 429
    assert send.call_count == 1


def test_device_when_old_tab_posts_should_reject(client: Client) -> None:
    """Старая вкладка не может восстановить отозванную привязку."""
    identity = _bind(client)
    client.logout()
    client.force_login(PushDevice.objects.get().user)
    assert _device(client, identity, enableAccount=True).status_code == 409


def test_confirmation_when_csrf_missing_should_reject(
    client: Client, mocker: MockerFixture
) -> None:
    """Подтверждение требует CSRF независимо от знания доставленного кода."""
    _, code = _start(client, mocker)
    protected = Client(enforce_csrf_checks=True)
    protected.cookies = client.cookies
    assert _confirm(protected, code).status_code == 403


def test_push_transport_when_called_should_target_subscription_only(
    mocker: MockerFixture,
) -> None:
    """ID подписки не расширяется провайдером на другие устройства через alias."""
    from apps.notifications.infrastructure.push import send_push

    response = mocker.Mock(status_code=200)
    response.json.return_value = {"id": "accepted-id"}
    post = mocker.patch(
        "apps.notifications.infrastructure.push.requests.post", return_value=response
    )
    assert (
        send_push(
            subscription_ids=[SUBSCRIPTION],
            title="Тема",
            body="Текст",
            url="https://tennisfan.ru/",
            key=SUBSCRIPTION,
        )
        == "accepted-id"
    )
    payload = post.call_args.kwargs["json"]
    assert payload["include_subscription_ids"] == [SUBSCRIPTION]
    assert "include_aliases" not in payload
    assert "included_segments" not in payload


def test_confirmation_when_opted_out_should_keep_rate_limit(
    client: Client, mocker: MockerFixture
) -> None:
    """Выключение не позволяет обойти минутный лимит повторных подтверждений."""
    identity, _ = _start(client, mocker)
    assert _device(client, identity, active=False).status_code == 200
    assert _device(client, identity, enableAccount=True).status_code == 429


@pytest.mark.django_db(transaction=True)
def test_confirmation_when_consumed_concurrently_should_activate_once(
    client: Client, mocker: MockerFixture
) -> None:
    """Два одновременных POST не потребляют один код дважды."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connections

    _, code = _start(client, mocker)

    def consume() -> int:
        try:
            other = Client()
            other.cookies.update(client.cookies)
            return _confirm(other, code).status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume(), range(2)))
    assert sorted(results) == [200, 409]


def test_confirmation_when_transport_fails_should_not_activate(
    client: Client, mocker: MockerFixture
) -> None:
    """Отказ API не превращается в подтверждённую подписку."""
    from apps.notifications.infrastructure.push import PushTransportError

    identity = _bind(client)
    mocker.patch(
        "apps.notifications.views.send_push", side_effect=PushTransportError("offline")
    )
    assert _device(client, identity, enableAccount=True).status_code == 503
    assert not PushDevice.objects.get().active


def test_confirmation_when_resending_should_reject_previous_code(
    client: Client, mocker: MockerFixture
) -> None:
    """После разрешённого повтора старый код больше не подтверждает устройство."""
    from apps.notifications.models import PushConfirmation

    identity, old_code = _start(client, mocker)
    PushConfirmation.objects.update(requested_at=timezone.now() - timedelta(minutes=2))
    send = mocker.patch(
        "apps.notifications.views.send_push", return_value="retry-message"
    )
    assert _device(client, identity, enableAccount=True).status_code == 202
    new_code = parse_qs(urlsplit(send.call_args.kwargs["url"]).fragment)["code"][0]
    assert old_code != new_code
    assert _confirm(client, old_code).status_code == 409
    assert _confirm(client, new_code).status_code == 200


def test_confirmation_when_csrf_valid_should_accept(
    client: Client, mocker: MockerFixture
) -> None:
    """Настоящая форма с CSRF проходит проверку в исходном браузере."""
    _, code = _start(client, mocker)
    protected = Client(enforce_csrf_checks=True)
    protected.cookies.update(client.cookies)
    page = protected.get(reverse("notification_confirm"))
    assert page.status_code == 200
    response = protected.post(
        reverse("notification_confirm"),
        {"confirm": "yes", "code": code},
        HTTP_X_CSRFTOKEN=protected.cookies["csrftoken"].value,
    )
    assert response.status_code == 200
    assert PushDevice.objects.get().active


@pytest.mark.parametrize("origin", ["null", "https://untrusted.example"])
def test_confirmation_when_https_origin_untrusted_should_reject(
    client: Client, mocker: MockerFixture, origin: str
) -> None:
    """Правильный код и CSRF-токен не обходят проверку источника HTTPS-запроса.

    Args:
        client: Исходный браузер с неподтверждённой подпиской.
        mocker: Изолированный транспорт подтверждения.
        origin: Непрозрачный или посторонний источник запроса.
    """
    _, code = _start(client, mocker)
    protected = Client(enforce_csrf_checks=True)
    protected.cookies.update(client.cookies)
    assert (
        protected.get(reverse("notification_confirm"), secure=True).status_code == 200
    )
    response = protected.post(
        reverse("notification_confirm"),
        {"confirm": "yes", "code": code},
        secure=True,
        HTTP_ORIGIN=origin,
        HTTP_X_CSRFTOKEN=protected.cookies["csrftoken"].value,
    )
    assert response.status_code == 403
    assert not PushDevice.objects.get().active


def test_worker_when_legacy_unverified_device_active_should_skip(
    client: Client, mocker: MockerFixture
) -> None:
    """Старые активные строки без подтверждённого subscription ID не адресуются."""
    from apps.notifications.services import create_notification
    from apps.notifications.worker import process_deliveries

    _bind(client)
    device = PushDevice.objects.get()
    PushDevice.objects.filter(pk=device.pk).update(active=True)
    NotificationPreference.objects.create(user=device.user, push_enabled=True)
    EventPolicy.objects.get_or_create(
        key="match.created", defaults={"title": "Матч", "group": "matches"}
    )
    create_notification(event_key="match.created", user=device.user, message="Матч")
    send = mocker.patch("apps.notifications.worker.send_push")
    process_deliveries(limit=10)
    send.assert_not_called()
