"""Диагностика push: устройства пользователя и отчёт OneSignal о доставке."""

from collections.abc import Iterator
from datetime import timedelta
from io import StringIO
from typing import Final

import pytest
import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone
from pytest_mock import MockerFixture

from apps.notifications.models import Delivery, PushDevice
from apps.users.models import User

pytestmark = pytest.mark.django_db
SUBSCRIPTION: Final = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def report_settings() -> Iterator[None]:
    """Задаёт фиктивные ключи OneSignal, чтобы не ходить в настоящий API.

    Yields:
        Изолированные настройки отчёта.
    """
    with override_settings(
        ONESIGNAL_APP_ID="test-app-id",
        ONESIGNAL_REST_API_KEY="test-key",
    ):
        yield


def _user(email: str = "report@example.test") -> User:
    """Создаёт пользователя отчёта."""
    return User.objects.create_user(email=email)


def _device(user: User, *, active: bool = True) -> PushDevice:
    """Создаёт подтверждённое устройство с известной подпиской."""
    return PushDevice.objects.create(
        user=user,
        device_hash="a" * 64,
        subscription_id=SUBSCRIPTION,
        active=active,
        expires_at=timezone.now() + timedelta(days=7),
    )


def _delivery(user: User, *, provider_id: str = "message-1") -> Delivery:
    """Создаёт принятое push-задание с ID сообщения провайдера."""
    now = timezone.now()
    return Delivery.objects.create(
        event_key="match.created",
        channel="push",
        user=user,
        title="Назначен матч",
        body="Текст",
        url="https://tennisfan.ru/matches/1/",
        status="accepted",
        provider_id=provider_id,
        available_at=now,
        expires_at=now + timedelta(days=1),
    )


def test_report_when_user_has_device_should_show_subscription_and_delivery() -> None:
    """Отчёт связывает подписку устройства с последним push-заданием."""
    user = _user()
    _device(user)
    _delivery(user)
    output = StringIO()
    call_command("notification_delivery_report", "--email", user.email, stdout=output)
    text = output.getvalue()
    assert SUBSCRIPTION in text
    assert "accepted" in text
    assert "message-1" in text
    assert "Устройства: 1" in text


def test_report_when_email_unknown_should_fail() -> None:
    """Опечатка в email не выдаёт отчёт по чужим данным."""
    with pytest.raises(CommandError):
        call_command(
            "notification_delivery_report",
            "--email",
            "missing@example.test",
            stdout=StringIO(),
        )


def test_report_when_provider_checked_should_print_counts(
    mocker: MockerFixture,
) -> None:
    """Счётчики OneSignal показывают фактическую доставку, а не только приём."""
    user = _user()
    _delivery(user, provider_id="message-2")
    response = mocker.Mock(status_code=200)
    response.json.return_value = {
        "id": "message-2",
        "successful": 1,
        "failed": 0,
        "errored": 0,
        "remaining": 0,
        "completed_at": 1761648000,
    }
    get = mocker.patch(
        "apps.notifications.infrastructure.push.requests.get",
        return_value=response,
    )
    output = StringIO()
    call_command(
        "notification_delivery_report",
        "--email",
        user.email,
        "--check-provider",
        stdout=output,
    )
    assert get.call_args.kwargs["params"] == {"app_id": "test-app-id"}
    assert get.call_args.kwargs["headers"] == {"Authorization": "Key test-key"}
    assert "successful=1" in output.getvalue()


def test_report_when_provider_unavailable_should_stay_safe(
    mocker: MockerFixture,
) -> None:
    """Сетевой сбой диагностики не скрывает локальный журнал доставок."""
    user = _user()
    _delivery(user)
    mocker.patch(
        "apps.notifications.infrastructure.push.requests.get",
        side_effect=requests.RequestException("boom"),
    )
    output = StringIO()
    call_command(
        "notification_delivery_report",
        "--email",
        user.email,
        "--check-provider",
        stdout=output,
    )
    assert "OneSignal: unavailable" in output.getvalue()


def test_report_when_key_missing_should_not_call_provider(
    mocker: MockerFixture,
) -> None:
    """Без REST-ключа отчёт остаётся локальным и не делает запросов."""
    user = _user()
    _delivery(user)
    get = mocker.patch("apps.notifications.infrastructure.push.requests.get")
    output = StringIO()
    with override_settings(ONESIGNAL_REST_API_KEY=""):
        call_command(
            "notification_delivery_report",
            "--email",
            user.email,
            "--check-provider",
            stdout=output,
        )
    get.assert_not_called()
    assert "OneSignal: not_configured" in output.getvalue()


def test_report_when_json_requested_should_return_machine_readable_data() -> None:
    """JSON-формат пригоден для копирования в поддержку без разбора текста."""
    user = _user()
    _device(user)
    _delivery(user)
    output = StringIO()
    call_command(
        "notification_delivery_report",
        "--email",
        user.email,
        "--json",
        stdout=output,
    )
    payload = output.getvalue()
    assert '"subscription_id"' in payload
    assert '"deliveries"' in payload
    assert "test-key" not in payload
