"""Регрессии poison pill, мгновенных chat-push и честного accepted."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Final

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from pytest_mock import MockerFixture

from apps.notifications.domain.catalog import EVENTS
from apps.notifications.infrastructure.push import PushRejectedError
from apps.notifications.models import (
    Delivery,
    EventPolicy,
    NotificationPreference,
    PushDevice,
)
from apps.notifications.presence import enter_chat
from apps.notifications.services import create_notification
from apps.notifications.worker import process_deliveries
from apps.users.models import Notification, User

pytestmark = pytest.mark.django_db
SUBSCRIPTION: Final = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def notification_settings(db: None) -> Iterator[None]:
    """Включает реестр, изолирует кэш и запрещает реальные отправки."""
    EventPolicy.objects.bulk_create(
        [
            EventPolicy(
                key=key, title=event.title, group=event.group, description=event.title
            )
            for key, event in EVENTS.items()
        ],
        ignore_conflicts=True,
    )
    cache.clear()
    with override_settings(
        NOTIFICATION_CENTER_ENABLED=True,
        SECURE_SSL_REDIRECT=False,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ALLOWED_HOSTS=["testserver"],
        ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru",
    ):
        yield
    cache.clear()


@pytest.fixture
def recipient() -> User:
    """Создаёт получателя с включённым push и активным устройством."""
    user = User.objects.create_user(email="worker@example.test")
    NotificationPreference.objects.create(user=user, push_enabled=True)
    PushDevice.objects.create(
        user=user,
        device_hash="a" * 64,
        active=True,
        subscription_id=SUBSCRIPTION,
        expires_at=timezone.now() + timedelta(days=1),
    )
    return user


def _push(recipient: User, event_key: str = "match.created") -> Delivery:
    return Delivery.objects.create(
        event_key=event_key,
        user=recipient,
        channel="push",
        title="Тема",
        body="Текст",
        url="https://tennisfan.ru/",
        available_at=timezone.now() - timedelta(seconds=1),
        expires_at=timezone.now() + timedelta(days=1),
    )


def test_worker_when_email_is_interrupted_should_continue_with_next_item(
    recipient: User, mocker: MockerFixture
) -> None:
    """Зависшее письмо не обрывает весь пакет и не отправляется повторно."""
    now = timezone.now()
    stale = Delivery.objects.create(
        event_key="manual.broadcast",
        channel="email",
        user=recipient,
        title="Письмо",
        body="Текст",
        url="https://tennisfan.ru/",
        status="processing",
        available_at=now - timedelta(minutes=10),
        expires_at=now + timedelta(days=1),
    )
    create_notification(event_key="match.created", user=recipient, message="Матч")
    send = mocker.patch(
        "apps.notifications.worker.send_push", return_value="provider-id"
    )
    assert process_deliveries(limit=10) == 1
    stale.refresh_from_db()
    assert stale.status == "uncertain"
    assert Delivery.objects.get(event_key="match.created").status == "accepted"
    send.assert_called_once()


def test_worker_when_event_policy_is_missing_should_fail_only_one_item(
    recipient: User, mocker: MockerFixture
) -> None:
    """Неизвестное событие не блокирует очередь навсегда."""
    _push(recipient, event_key="unknown.event")
    _push(recipient)
    send = mocker.patch(
        "apps.notifications.worker.send_push", return_value="provider-id"
    )
    assert process_deliveries(limit=10) == 2
    assert Delivery.objects.get(event_key="unknown.event").error_code == "unknown_event"
    assert Delivery.objects.get(event_key="match.created").status == "accepted"
    send.assert_called_once()


def test_worker_when_policy_row_deleted_should_not_crash(
    recipient: User, mocker: MockerFixture
) -> None:
    """Удалённая строка политики даёт понятную ошибку, а не падение команды."""
    EventPolicy.objects.filter(key="match.created").delete()
    _push(recipient)
    mocker.patch("apps.notifications.worker.send_push", return_value="provider-id")
    assert process_deliveries(limit=10) == 1
    item = Delivery.objects.get()
    assert item.status == "failed"
    assert item.error_code == "event_policy_missing"


def test_worker_when_unexpected_error_should_retry_not_stick(
    recipient: User, mocker: MockerFixture
) -> None:
    """Неизвестный сбой возвращает задание в очередь с ограниченным числом попыток."""
    _push(recipient)
    mocker.patch(
        "apps.notifications.worker.send_push", side_effect=RuntimeError("boom")
    )
    for _ in range(5):
        Delivery.objects.update(available_at=timezone.now())
        process_deliveries(limit=1)
    item = Delivery.objects.get()
    assert item.status == "failed"
    assert item.error_code == "worker_internal_error"
    assert item.attempts == 5


def test_chat_when_message_arrives_should_queue_push_immediately(
    recipient: User, mocker: MockerFixture
) -> None:
    """Сообщение вне чата сразу создаёт отдельное push без склейки."""
    room_id = 42
    create_notification(
        event_key="chat.direct_message",
        user=recipient,
        message="У вас новое сообщение в личном чате.",
        url=f"/chats/{room_id}/",
        chat_room_id=room_id,
    )
    item = Delivery.objects.get()
    assert item.chat_room_id == room_id
    assert item.available_at <= timezone.now()
    send = mocker.patch(
        "apps.notifications.worker.send_push", return_value="provider-id"
    )
    process_deliveries(limit=10)
    send.assert_called_once()
    assert "новое сообщение" in send.call_args.kwargs["body"]
    assert Delivery.objects.get().status == "accepted"


def test_chat_when_two_messages_arrive_should_create_two_immediate_pushes(
    recipient: User,
) -> None:
    """Каждое новое сообщение получает собственное задание без ожидания окна."""
    room_id = 42
    for _ in range(2):
        create_notification(
            event_key="chat.direct_message",
            user=recipient,
            message="У вас новое сообщение в личном чате.",
            url=f"/chats/{room_id}/",
            chat_room_id=room_id,
        )
    items = list(Delivery.objects.filter(channel="push"))
    assert len(items) == 2
    assert all(item.chat_room_id == room_id for item in items)
    assert all(item.available_at <= timezone.now() for item in items)


def test_chat_when_room_is_open_should_not_create_push(recipient: User) -> None:
    """Пока пользователь в комнате, уведомление остаётся только в ЛК."""
    enter_chat(user_id=recipient.pk, room_id=42)
    note = create_notification(
        event_key="chat.direct_message",
        user=recipient,
        message="У вас новое сообщение в личном чате.",
        url="/chats/42/",
        chat_room_id=42,
    )
    assert Notification.objects.filter(pk=note.pk).exists()
    assert not Delivery.objects.filter(channel="push").exists()


def test_chat_when_room_opens_should_cancel_pending_push(recipient: User) -> None:
    """Переход в комнату отменяет ещё не отправленный push."""
    create_notification(
        event_key="chat.direct_message",
        user=recipient,
        message="У вас новое сообщение в личном чате.",
        url="/chats/42/",
        chat_room_id=42,
    )
    enter_chat(user_id=recipient.pk, room_id=42)
    item = Delivery.objects.get()
    assert item.status == "skipped"
    assert item.error_code == "seen_in_chat"


def test_worker_when_user_opens_room_after_claim_should_skip_push(
    recipient: User, mocker: MockerFixture
) -> None:
    """Проверка присутствия выполняется и перед сетевой отправкой."""
    room_id = 42
    create_notification(
        event_key="chat.direct_message",
        user=recipient,
        message="У вас новое сообщение в личном чате.",
        url=f"/chats/{room_id}/",
        chat_room_id=room_id,
    )
    cache.set(f"notifications:chat-presence:{recipient.pk}:{room_id}", 1, timeout=120)
    send = mocker.patch(
        "apps.notifications.worker.send_push", return_value="provider-id"
    )
    process_deliveries(limit=10)
    send.assert_not_called()
    assert Delivery.objects.get().error_code == "seen_in_chat"


def test_worker_when_provider_reports_no_recipients_should_deactivate_device(
    recipient: User, mocker: MockerFixture
) -> None:
    """Ответ `recipients=0` не оставляет мёртвую подписку активной."""
    _push(recipient)
    mocker.patch(
        "apps.notifications.worker.send_push",
        side_effect=PushRejectedError("push_no_recipients"),
    )
    process_deliveries(limit=1)
    assert not PushDevice.objects.get(user=recipient).active
    item = Delivery.objects.get()
    assert item.status == "failed"
    assert item.error_code == "push_no_recipients"
