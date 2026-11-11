"""Уведомление проверяющих о новой заявке законного представителя."""

from collections.abc import Iterator
from datetime import date

import pytest
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import override_settings
from django.urls import reverse

from apps.juniors.models import LegalRelease
from apps.juniors.services import create_child
from apps.notifications.models import Delivery, NotificationPreference
from apps.users.models import Notification
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def settings_for_notifications() -> Iterator[None]:
    """Запрещает реальные внешние отправки и задаёт общий адрес администрации."""
    with override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        NOTIFICATION_CENTER_ENABLED=True,
        ADMIN_NOTIFICATIONS_EMAIL="admin@example.test",
        SITE_URL="https://tennisfan.ru",
        ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru",
    ):
        yield


def release() -> LegalRelease:
    """Создаёт утверждённую версию согласия для оформления профиля."""
    return LegalRelease.objects.create(
        version="guardian-notify",
        processing_text="Тест обработки",
        publication_text="Тест публикации",
        reviewed=True,
        is_current=True,
    )


def details(document: LegalRelease) -> dict[str, object]:
    """Возвращает корректные данные формы добавления ребёнка."""
    return {
        "first_name": "Анна",
        "last_name": "Иванова",
        "birth_date": date(2018, 5, 10),
        "city": "Москва",
        "gender": "female",
        "ball": "red",
        "processing_accepted": True,
        "representative": True,
        "accurate": True,
        "legal_release": document,
    }


def reviewer(email: str = "verifier@junior.test", push: bool = True):
    """Создаёт сотрудника с правом проверки представителей."""
    user = make_user(email=email, is_staff=True)
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="juniors", codename="verify_representative"
        )
    )
    if push:
        NotificationPreference.objects.create(user=user, push_enabled=True)
    return user


def test_pending_guardian_notifies_reviewers_via_inbox_push_and_email(
    django_capture_on_commit_callbacks,
) -> None:
    """Новая заявка доходит до проверяющих в ЛК, push и на общий адрес."""
    parent = make_user(email="parent@junior.test", email_verified=True)
    verifier = reviewer()
    without_rights = make_user(email="manager@junior.test", is_staff=True)
    with django_capture_on_commit_callbacks(execute=True):
        create_child(actor=parent, data=details(release()), relation="parent")

    note = Notification.objects.get(user=verifier)
    assert note.event_key == "junior.guardian_pending"
    assert note.url == reverse("admin:juniors_guardianlink_changelist")
    assert "Анна Иванова" in note.message
    assert "parent@junior.test" in note.message
    assert set(
        Delivery.objects.filter(notification=note).values_list("channel", flat=True)
    ) == {"push"}
    assert not Notification.objects.filter(user=without_rights).exists()
    assert [message.to for message in mail.outbox] == [["admin@example.test"]]
    assert "Анна Иванова" in mail.outbox[0].body
    assert note.url in mail.outbox[0].body


@override_settings(NOTIFICATION_CENTER_ENABLED=False)
def test_pending_guardian_keeps_email_when_center_is_disabled(
    django_capture_on_commit_callbacks,
) -> None:
    """При отключённом центре остаются уведомление в ЛК и письмо."""
    parent = make_user(email="parent-off@junior.test", email_verified=True)
    verifier = reviewer(email="verifier-off@junior.test")
    with django_capture_on_commit_callbacks(execute=True):
        create_child(actor=parent, data=details(release()), relation="parent")
    assert Notification.objects.filter(
        user=verifier, event_key="junior.guardian_pending"
    ).exists()
    assert not Delivery.objects.exists()
    assert [message.to for message in mail.outbox] == [["admin@example.test"]]


def test_pending_guardian_does_not_email_without_shared_address(
    django_capture_on_commit_callbacks,
) -> None:
    """Без настроенного общего адреса письмо не отправляется, ЛК сохраняется."""
    parent = make_user(email="parent-empty@junior.test", email_verified=True)
    verifier = reviewer(email="verifier-empty@junior.test")
    with (
        override_settings(ADMIN_NOTIFICATIONS_EMAIL=""),
        django_capture_on_commit_callbacks(execute=True),
    ):
        create_child(actor=parent, data=details(release()), relation="parent")
    assert Notification.objects.filter(user=verifier).exists()
    assert not mail.outbox
