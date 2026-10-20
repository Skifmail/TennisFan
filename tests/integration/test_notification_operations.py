"""Проверки мониторинга очереди и ретенции технических записей."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.notifications.models import Delivery, PushDevice
from apps.users.models import User

pytestmark = pytest.mark.django_db


def _delivery(user: User, **extra: object) -> Delivery:
    now = timezone.now()
    values = {
        "event_key": "match.created",
        "channel": "push",
        "user": user,
        "title": "Тема",
        "body": "Текст",
        "url": "https://tennisfan.ru/",
        "available_at": now,
        "expires_at": now + timedelta(days=1),
    }
    values.update(extra)
    return Delivery.objects.create(**values)


def test_queue_status_when_empty_should_succeed() -> None:
    """Пустая очередь не считается отставанием."""
    call_command("notification_queue_status", stdout=StringIO())


def test_queue_status_when_due_item_is_old_should_fail() -> None:
    """Просроченная очередь даёт ненулевой код для мониторинга."""
    user = User.objects.create_user(email="queue@example.test")
    _delivery(user, available_at=timezone.now() - timedelta(hours=1))
    with pytest.raises(CommandError):
        call_command("notification_queue_status", stdout=StringIO())


def test_retention_when_dry_run_should_not_delete() -> None:
    """Предпросмотр показывает объём, не меняя данные."""
    user = User.objects.create_user(email="retention@example.test")
    old = timezone.now() - timedelta(days=40)
    _delivery(user, status="accepted", finished_at=old)
    PushDevice.objects.create(
        user=user,
        device_hash="r" * 64,
        active=False,
        expires_at=old,
    )
    call_command("cleanup_notification_history", "--dry-run", stdout=StringIO())
    assert Delivery.objects.exists()
    assert PushDevice.objects.exists()


def test_retention_when_confirmed_should_remove_only_old_records() -> None:
    """Завершённые задания и мёртвые устройства удаляются, свежие остаются."""
    user = User.objects.create_user(email="retention-two@example.test")
    old = timezone.now() - timedelta(days=40)
    _delivery(user, status="accepted", finished_at=old)
    fresh = _delivery(user, status="accepted", finished_at=timezone.now())
    PushDevice.objects.create(
        user=user,
        device_hash="s" * 64,
        active=False,
        expires_at=old,
    )
    call_command("cleanup_notification_history", stdout=StringIO())
    assert Delivery.objects.filter(pk=fresh.pk).exists()
    assert not PushDevice.objects.exists()


def test_retention_when_queued_item_is_old_should_keep_it() -> None:
    """Незавершённое задание не удаляется, даже если оно давно в очереди."""
    user = User.objects.create_user(email="retention-queued@example.test")
    old = timezone.now() - timedelta(days=40)
    queued = _delivery(user, available_at=old)
    call_command("cleanup_notification_history", stdout=StringIO())
    assert Delivery.objects.filter(pk=queued.pk).exists()
