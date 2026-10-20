"""Подключение устройства кодом из push без перехода по уведомлению."""

import hashlib
import json
import re
from datetime import timedelta
from typing import cast

import pytest
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pytest_mock import MockerFixture

from apps.notifications.models import PushConfirmation, PushDevice
from tests.integration.test_notification_identity import SUBSCRIPTION, _bind
from tests.integration.test_notification_identity import (
    identity_settings as identity_settings,
)

pytestmark = pytest.mark.django_db


def _request(client: Client, identity: dict[str, object]) -> HttpResponse:
    return cast(
        HttpResponse,
        client.post(
            reverse("notification_code_request"),
            data=json.dumps(
                {
                    "externalId": identity["externalId"],
                    "subscriptionId": SUBSCRIPTION,
                    "active": True,
                    "enableAccount": True,
                }
            ),
            content_type="application/json",
        ),
    )


def _start(client: Client, mocker: MockerFixture) -> tuple[dict[str, object], str]:
    identity = _bind(client)
    send = mocker.patch(
        "apps.notifications.views.send_push", return_value="test-message"
    )
    response = _request(client, identity)
    assert response.status_code == 202
    code = re.search(r"\b[0-9]{6}\b", send.call_args.kwargs["body"])
    assert code is not None
    value = code.group()
    assert value not in response.content.decode()
    assert value not in send.call_args.kwargs["url"]
    assert (
        PushConfirmation.objects.get().token_hash
        != hashlib.sha256(value.encode()).hexdigest()
    )
    return identity, value


def _verify(client: Client, identity: dict[str, object], code: str) -> HttpResponse:
    return cast(
        HttpResponse,
        client.post(
            reverse("notification_code_verify"),
            data=json.dumps({"externalId": identity["externalId"], "code": code}),
            content_type="application/json",
        ),
    )


def test_numeric_code_when_delivered_should_activate_once_without_click(
    client: Client, mocker: MockerFixture
) -> None:
    """Доставленный код подключает исходное устройство однократно без ссылки."""
    identity, code = _start(client, mocker)
    assert not PushDevice.objects.get().active
    verified = _verify(client, identity, code)
    assert verified.status_code == 200
    assert json.loads(verified.content)["verifiedSubscriptionId"] == SUBSCRIPTION
    assert PushDevice.objects.get().active
    assert _verify(client, identity, code).status_code == 409


@pytest.mark.parametrize(
    "condition", ["expired", "other_browser", "generation", "opt_out"]
)
def test_numeric_code_when_binding_invalid_should_not_activate(
    client: Client, mocker: MockerFixture, condition: str
) -> None:
    """Истёкший код и чужая либо отозванная привязка не активируют устройство."""
    identity, code = _start(client, mocker)
    if condition == "expired":
        PushConfirmation.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    elif condition == "other_browser":
        other = Client()
        other.force_login(PushDevice.objects.get().user)
        client = other
    elif condition == "generation":
        identity["externalId"] = "00000000-0000-4000-8000-000000000099"
    else:
        from tests.integration.test_notification_identity import _device

        _device(client, identity, active=False)
    assert _verify(client, identity, code).status_code == 409
    assert not PushDevice.objects.get().active


def test_numeric_code_when_guessed_should_limit_attempts_and_preserve_limit_on_resend(
    client: Client, mocker: MockerFixture
) -> None:
    """Пять ошибок блокируют подбор даже после запроса нового кода."""
    identity, code = _start(client, mocker)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        assert _verify(client, identity, wrong).status_code == 400
    assert _verify(client, identity, wrong).status_code == 429
    PushConfirmation.objects.update(requested_at=timezone.now() - timedelta(minutes=2))
    assert _request(client, identity).status_code == 202
    assert _verify(client, identity, code).status_code == 429
    assert not PushDevice.objects.get().active


def test_numeric_code_when_sent_to_legacy_form_should_not_bypass_attempt_limit(
    client: Client, mocker: MockerFixture
) -> None:
    """Старый маршрут подтверждения не принимает короткие коды."""
    _, code = _start(client, mocker)
    assert (
        client.post(
            reverse("notification_confirm"), {"code": code, "confirm": "yes"}
        ).status_code
        == 409
    )
    assert not PushDevice.objects.get().active


def test_numeric_code_when_post_has_no_csrf_should_reject(
    client: Client, mocker: MockerFixture
) -> None:
    """Проверка кода сохраняет обязательную CSRF-защиту."""
    identity, code = _start(client, mocker)
    protected = Client(enforce_csrf_checks=True)
    protected.cookies = client.cookies
    assert _verify(protected, identity, code).status_code == 403


def test_numeric_code_when_resent_should_invalidate_previous_code(
    client: Client, mocker: MockerFixture
) -> None:
    """Повторная отправка оставляет действительным только последний код."""
    identity, old = _start(client, mocker)
    PushConfirmation.objects.update(requested_at=timezone.now() - timedelta(minutes=2))
    mocker.patch(
        "apps.notifications.device_binding.secrets.randbelow",
        return_value=123456 if old != "123456" else 654321,
    )
    send = mocker.patch("apps.notifications.views.send_push", return_value="next")
    assert _request(client, identity).status_code == 202
    match = re.search(r"\b[0-9]{6}\b", send.call_args.kwargs["body"])
    assert match is not None
    assert _verify(client, identity, old).status_code == 400
    assert _verify(client, identity, match.group()).status_code == 200


def test_numeric_code_when_attempt_window_expires_should_allow_new_confirmation(
    client: Client, mocker: MockerFixture
) -> None:
    """Истечение общего окна разблокирует верный код без сброса аккаунта."""
    from apps.notifications.models import PushCodeLimit

    identity, code = _start(client, mocker)
    PushCodeLimit.objects.create(
        user=PushDevice.objects.get().user,
        failures=5,
        resets_at=timezone.now() - timedelta(seconds=1),
    )
    assert _verify(client, identity, code).status_code == 200


def test_numeric_code_when_other_browser_retries_should_share_account_limit(
    client: Client, mocker: MockerFixture
) -> None:
    """Новый браузер и новая подписка не обходят пять попыток на аккаунт."""
    from apps.notifications.models import PushCodeLimit

    identity, _ = _start(client, mocker)
    user = PushDevice.objects.get().user
    PushCodeLimit.objects.create(
        user=user, failures=5, resets_at=timezone.now() + timedelta(minutes=5)
    )
    other = Client()
    other.force_login(user)
    identity = other.post(reverse("notification_identity")).json()
    response = other.post(
        reverse("notification_code_request"),
        data=json.dumps(
            {
                "externalId": identity["externalId"],
                "subscriptionId": "00000000-0000-4000-8000-000000000002",
                "active": True,
                "enableAccount": True,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 202
    assert _verify(other, identity, "123456").status_code == 429


def test_numeric_code_when_page_reloads_should_restore_pending_form_state(
    client: Client, mocker: MockerFixture
) -> None:
    """Повторное открытие страницы восстанавливает ввод, но не раскрывает код."""
    _, code = _start(client, mocker)
    response = client.post(reverse("notification_identity"))
    assert response.json()["pendingCode"] is True
    assert code not in response.content.decode()
