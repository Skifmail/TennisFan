"""Прямая доставка уведомлений поддержки всем администраторам без зависимости от email."""

from unittest.mock import patch

import pytest

from apps.core.models import BugReport
from apps.core.support_notifications import send_admin_support_notification
from apps.core.support_service import SupportAuthor, create_user_message
from apps.notifications.models import Delivery, EventPolicy, NotificationPreference
from apps.users.models import Notification
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def notification_settings(settings):
    """Включает реальную очередь и подменяет только внешний почтовый транспорт."""
    settings.NOTIFICATION_CENTER_ENABLED = True
    settings.ADMIN_NOTIFICATIONS_EMAIL = "separate-mailbox@support.test"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.ONESIGNAL_SITE_ORIGIN = "https://tennisfan.ru"
    for key in ("support.admin_message", "support.bug_report"):
        EventPolicy.objects.update_or_create(
            key=key,
            defaults={
                "title": key,
                "group": "Поддержка",
                "push_enabled": True,
                "inbox_enabled": True,
                "email_enabled": True,
            },
        )


def admin(email, **kwargs):
    """Создаёт сотрудника с явным согласием на push."""
    user = make_user(email=email, **kwargs)
    NotificationPreference.objects.create(user=user, push_enabled=True)
    return user


def message():
    """Создаёт исходное обращение без внешних уведомлений."""
    user = make_user(email="author@support.test")
    return create_user_message(
        author=SupportAuthor(user.pk, "", "", ""),
        text="Подробности, которые не должны попасть на экран блокировки",
    )


@pytest.mark.parametrize("is_bug", [False, True])
def test_all_active_admins_receive_push_even_when_email_is_different(is_bug):
    first = admin("first@support.test", is_staff=True)
    second = admin("second@support.test", is_superuser=True)
    admin("inactive@support.test", is_staff=True, is_active=False)
    admin("ordinary@support.test")
    source = message()
    if is_bug:
        BugReport.objects.create(message=source)
    assert send_admin_support_notification(source)
    expected = "support.bug_report" if is_bug else "support.admin_message"
    pushes = Delivery.objects.filter(channel="push", event_key=expected)
    assert set(pushes.values_list("user_id", flat=True)) == {first.pk, second.pk}
    assert Notification.objects.filter(event_key=expected).count() == 2
    for delivery in pushes:
        assert delivery.url.endswith(f"/platform/support/{source.thread_id}/")
        assert source.text not in delivery.body


@pytest.mark.parametrize("mailbox", ["", "first@support.test"])
@pytest.mark.parametrize("email_enabled", [False, True])
def test_email_disabled_or_missing_does_not_block_or_duplicate_push(
    settings, mailbox, email_enabled
):
    first = admin("first@support.test", is_staff=True)
    settings.ADMIN_NOTIFICATIONS_EMAIL = mailbox
    EventPolicy.objects.filter(pk="support.admin_message").update(
        email_enabled=email_enabled
    )
    send_admin_support_notification(message())
    assert Delivery.objects.filter(user=first, channel="push").count() == 1
    assert Notification.objects.filter(user=first).count() == 1


def test_smtp_failure_does_not_lose_push():
    first = admin("first@support.test", is_staff=True)
    with patch(
        "apps.core.support_notifications._send_with_timeout",
        side_effect=OSError("SMTP unavailable"),
    ):
        assert not send_admin_support_notification(message())
    assert Delivery.objects.filter(user=first, channel="push").count() == 1


def test_disabled_push_and_missing_consent_are_respected():
    first = admin("first@support.test", is_staff=True)
    NotificationPreference.objects.filter(user=first).update(push_enabled=False)
    send_admin_support_notification(message())
    assert Notification.objects.filter(user=first).count() == 1
    assert not Delivery.objects.filter(channel="push").exists()


def test_policy_disables_push_without_disabling_inbox():
    first = admin("first@support.test", is_staff=True)
    EventPolicy.objects.filter(pk="support.admin_message").update(push_enabled=False)
    send_admin_support_notification(message())
    assert Notification.objects.filter(user=first).count() == 1
    assert not Delivery.objects.filter(channel="push").exists()
