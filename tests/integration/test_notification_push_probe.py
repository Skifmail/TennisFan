"""Проверки пробного push через боевую очередь доставки."""

from collections.abc import Iterator
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone
from pytest_mock import MockerFixture

from apps.notifications.management.commands import notification_push_probe
from apps.notifications.models import (
    Delivery,
    EventPolicy,
    NotificationPreference,
    PushDevice,
)
from apps.users.models import Notification, User

pytestmark = pytest.mark.django_db
SUBSCRIPTION = "00000000-0000-4000-8000-000000000002"


@pytest.fixture(autouse=True)
def probe_settings() -> Iterator[None]:
    """Включает центр уведомлений и фиксирует канонический origin.

    Yields:
        Изолированные настройки пробной доставки.
    """
    with override_settings(
        NOTIFICATION_CENTER_ENABLED=True,
        ONESIGNAL_APP_ID="test-app-id",
        ONESIGNAL_REST_API_KEY="test-key",
        ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru",
    ):
        yield


def _user(*, push_enabled: bool = True) -> User:
    """Создаёт получателя с явным согласием на push."""
    user = User.objects.create_user(email="probe@example.test")
    NotificationPreference.objects.create(user=user, push_enabled=push_enabled)
    return user


def _device(user: User, *, active: bool = True) -> PushDevice:
    """Создаёт подтверждённое устройство с известной подпиской."""
    return PushDevice.objects.create(
        user=user,
        device_hash="b" * 64,
        subscription_id=SUBSCRIPTION,
        active=active,
        expires_at=timezone.now() + timedelta(days=7),
    )


def test_probe_when_worker_accepts_should_report_accepted(
    mocker: MockerFixture,
) -> None:
    """Задание проходит тот же путь, что и рабочие события, и получает статус."""
    user = _user()
    _device(user)

    def accept(self: object, item: Delivery, seconds: int) -> Delivery:
        Delivery.objects.filter(pk=item.pk).update(
            status="accepted", provider_id="message-1", attempts=1
        )
        item.refresh_from_db()
        return item

    mocker.patch.object(notification_push_probe.Command, "_wait", accept)
    mocker.patch(
        "apps.notifications.management.commands.notification_push_probe.fetch_delivery_stats",
        return_value={"successful": 1, "failed": 0},
    )
    output = StringIO()
    call_command(
        "notification_push_probe",
        "--email",
        user.email,
        "--wait",
        "10",
        "--check-provider",
        stdout=output,
    )
    text = output.getvalue()
    item = Delivery.objects.get(user=user)
    assert item.status == "accepted"
    assert item.channel == "push"
    assert item.url == "https://tennisfan.ru/users/notifications/"
    assert SUBSCRIPTION in text
    assert "successful=1" in text
    # Пробный push не создаёт запись в ЛК: это диагностика, а не событие.
    assert not Notification.objects.filter(user=user).exists()


def test_probe_when_worker_skips_should_fail_visibly() -> None:
    """Необработанное задание не выдаёт себя за успешную доставку."""
    user = _user()
    _device(user)
    output = StringIO()
    with pytest.raises(CommandError):
        call_command(
            "notification_push_probe",
            "--email",
            user.email,
            "--wait",
            "0",
            stdout=output,
        )
    assert Delivery.objects.get(user=user).status == "queued"
    assert "Проверка уведомлений TennisFan" in Delivery.objects.get(user=user).title


def test_probe_when_push_disabled_should_not_queue() -> None:
    """Выключенное согласие объясняется до создания задания."""
    user = _user(push_enabled=False)
    _device(user)
    with pytest.raises(CommandError, match="выключены push-уведомления"):
        call_command(
            "notification_push_probe",
            "--email",
            user.email,
            "--wait",
            "0",
            stdout=StringIO(),
        )
    assert not Delivery.objects.exists()


def test_probe_without_active_device_should_not_queue() -> None:
    """Без подтверждённой подписки worker не смог бы отправить push."""
    user = _user()
    _device(user, active=False)
    with pytest.raises(CommandError, match="Нет активных подтверждённых устройств"):
        call_command(
            "notification_push_probe",
            "--email",
            user.email,
            "--wait",
            "0",
            stdout=StringIO(),
        )
    assert not Delivery.objects.exists()


def test_probe_when_event_push_disabled_should_not_queue() -> None:
    """Политика события проверяется до постановки задания."""
    user = _user()
    _device(user)
    EventPolicy.objects.filter(key="match.created").update(push_enabled=False)
    with pytest.raises(CommandError, match="выключен администратором"):
        call_command(
            "notification_push_probe",
            "--email",
            user.email,
            "--wait",
            "0",
            stdout=StringIO(),
        )
    assert not Delivery.objects.exists()


def test_probe_when_email_unknown_should_fail() -> None:
    """Опечатка в email не ставит задание чужому получателю."""
    with pytest.raises(CommandError, match="не найден"):
        call_command(
            "notification_push_probe",
            "--email",
            "missing@example.test",
            stdout=StringIO(),
        )
