"""Приёмка политики каналов, очереди и защищённых ручных рассылок."""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from django.core import mail
from django.db import transaction
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from pytest_mock import MockerFixture

from apps.notifications.domain.rules import NotificationValidationError
from apps.notifications.infrastructure.email import EventEmail
from apps.notifications.models import (
    Broadcast,
    Delivery,
    EventPolicy,
    NotificationPreference,
    PushDevice,
)
from apps.notifications.services import (
    create_notification,
    expand_broadcast,
    queue_broadcast,
)
from apps.notifications.worker import process_deliveries
from apps.users.models import Notification, User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def notification_settings(db: None) -> Iterator[None]:
    """Запрещает реальные отправки и включает новый механизм только для тестов."""
    from apps.notifications.domain.catalog import EVENTS

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
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ALLOWED_HOSTS=["testserver"],
        ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru",
    ):
        yield


@pytest.fixture
def recipient() -> User:
    """Создаёт отдельного получателя без настоящего адреса."""
    return User.objects.create_user(email="notification@example.test")


def test_inbox_when_preferences_off_should_preserve_new_and_old(
    recipient: User,
) -> None:
    """Выключение двух внешних каналов не скрывает новые записи и историю."""
    NotificationPreference.objects.create(
        user=recipient, push_enabled=False, email_enabled=False
    )
    old = Notification.objects.create(user=recipient, message="История")
    note = create_notification(
        event_key="match.created", user=recipient, message="Матч", url="/tournaments/"
    )
    assert (
        Notification.objects.filter(pk__in=[old.pk, note.pk], is_visible=True).count()
        == 2
    )
    assert not Delivery.objects.exists()


def test_inbox_when_admin_disables_should_hide_only_future_records(
    recipient: User,
) -> None:
    """Связанные служебные записи сохраняют PK, но не попадают в ленту."""
    old = Notification.objects.create(user=recipient, message="Старое")
    EventPolicy.objects.filter(key="match.created").update(inbox_enabled=False)
    note = create_notification(
        event_key="match.created", user=recipient, message="Новое"
    )
    assert note.pk is not None
    assert not note.is_visible
    old.refresh_from_db()
    assert old.is_visible


def test_event_when_email_and_inbox_should_queue_one_push(recipient: User) -> None:
    """У одного события один канонический источник push, не два канала-дублёра."""
    NotificationPreference.objects.create(user=recipient, push_enabled=True)
    create_notification(
        event_key="match.created", user=recipient, message="Назначен матч"
    )
    EventEmail(
        subject="Матч", body="Текст", to=[recipient.email], event_key="match.created"
    ).send()
    assert Delivery.objects.filter(channel="push").count() == 1


def test_email_when_disabled_should_still_capture_email_only_event(
    recipient: User,
) -> None:
    """Запрет email не лишает пользователя ЛК и push для почтового события."""
    NotificationPreference.objects.create(
        user=recipient, push_enabled=True, email_enabled=False
    )
    sent = EventEmail(
        subject="Личное письмо",
        body="Секретные подробности",
        to=[recipient.email],
        event_key="subscription.purchased",
    ).send()
    assert sent == 0
    assert len(mail.outbox) == 0
    assert (
        Notification.objects.filter(
            user=recipient, event_key="subscription.purchased", is_visible=True
        ).count()
        == 1
    )
    delivery = Delivery.objects.get(channel="push")
    assert "Секретные" not in delivery.body


def test_push_when_transaction_rolls_back_should_not_escape(recipient: User) -> None:
    """Задание доставки откатывается вместе с бизнес-действием."""
    NotificationPreference.objects.create(user=recipient, push_enabled=True)
    with pytest.raises(RuntimeError), transaction.atomic():
        create_notification(event_key="match.created", user=recipient, message="Матч")
        raise RuntimeError("rollback")
    assert not Delivery.objects.exists()


def test_broadcast_when_queued_twice_should_not_repeat_or_edit(recipient: User) -> None:
    """Повторное подтверждение одной рассылки не создаёт повторных заданий."""
    campaign = Broadcast.objects.create(
        title="Проверка", body="Новый турнир", send_inbox=True, send_push=True
    )
    queue_broadcast(campaign.pk, actor=recipient)
    queue_broadcast(campaign.pk, actor=recipient)
    expand_broadcast(campaign.pk)
    expand_broadcast(campaign.pk)
    assert (
        Notification.objects.filter(
            user=recipient, event_key="manual.broadcast"
        ).count()
        == 1
    )
    assert Delivery.objects.filter(broadcast=campaign, channel="push").count() == 0


def test_broadcast_when_limit_exceeded_should_not_queue(recipient: User) -> None:
    """Ограничения проверяются сервисом независимо от формы и модели."""
    campaign = Broadcast.objects.create(title="Тема", body="Т" * 121, send_inbox=True)
    with pytest.raises(NotificationValidationError):
        queue_broadcast(campaign.pk, actor=recipient)
    assert not Delivery.objects.exists()


def test_worker_when_preference_revoked_should_skip_pending_push(
    recipient: User, mocker: MockerFixture
) -> None:
    """Перед отправкой повторно проверяется отказ пользователя."""
    preference = NotificationPreference.objects.create(
        user=recipient, push_enabled=True
    )
    create_notification(event_key="match.created", user=recipient, message="Матч")
    preference.push_enabled = False
    preference.save()
    send = mocker.patch("apps.notifications.worker.send_push")
    process_deliveries(limit=10)
    send.assert_not_called()
    assert Delivery.objects.get().status == "skipped"


def test_worker_when_push_succeeds_should_not_send_again(
    recipient: User, mocker: MockerFixture
) -> None:
    """Обработанное задание не попадает в следующую итерацию очереди."""
    NotificationPreference.objects.create(user=recipient, push_enabled=True)
    PushDevice.objects.create(
        user=recipient,
        device_hash="a" * 64,
        active=True,
        subscription_id="00000000-0000-4000-8000-000000000001",
        expires_at=timezone.now() + timedelta(days=1),
    )
    create_notification(event_key="match.created", user=recipient, message="Матч")
    send = mocker.patch(
        "apps.notifications.worker.send_push", return_value="provider-id"
    )
    process_deliveries(limit=10)
    process_deliveries(limit=10)
    assert send.call_count == 1
    assert send.call_args.kwargs["subscription_ids"] == [
        "00000000-0000-4000-8000-000000000001"
    ]
    assert Delivery.objects.get().status == "accepted"


def test_preferences_when_posted_should_change_only_current_user(
    client: Client, recipient: User
) -> None:
    """Форма содержит только два параметра и не принимает чужой user_id."""
    other = User.objects.create_user(email="other@example.test")
    client.force_login(recipient)
    response = client.post(
        reverse("notification_settings"),
        {"push_enabled": "on", "email_enabled": "on", "user_id": other.pk},
    )
    assert response.status_code == 302
    assert NotificationPreference.objects.get(user=recipient).push_enabled
    assert not NotificationPreference.objects.filter(user=other).exists()


def test_preferences_when_csrf_missing_should_reject(recipient: User) -> None:
    """Изменения настроек защищены CSRF, а GET ничего не сохраняет."""
    client = Client(enforce_csrf_checks=True)
    client.force_login(recipient)
    assert (
        client.post(
            reverse("notification_settings"), {"email_enabled": "on"}
        ).status_code
        == 403
    )


def test_broadcast_admin_when_unprivileged_should_deny(
    client: Client, recipient: User
) -> None:
    """Отправка требует отдельного права, одного is_staff недостаточно."""
    recipient.is_staff = True
    recipient.save()
    client.force_login(recipient)
    campaign = Broadcast.objects.create(title="Тема", body="Текст", send_inbox=True)
    response = client.post(
        reverse("admin:notifications_broadcast_send", args=[campaign.pk]),
        {"confirm": "yes"},
    )
    assert response.status_code == 403


def test_email_when_legacy_channel_disabled_should_keep_other_channels(
    recipient: User,
) -> None:
    """Старое ограничение email не препятствует новому независимому событию."""
    NotificationPreference.objects.create(user=recipient, push_enabled=True)
    EventEmail(
        event_key="club.fee_reminder",
        delivery_allowed=False,
        subject="Взнос",
        body="Подробности",
        to=[recipient.email],
    ).send()
    assert len(mail.outbox) == 0
    assert Delivery.objects.filter(channel="push").count() == 1
    assert (
        Notification.objects.filter(
            event_key="club.fee_reminder", is_visible=True
        ).count()
        == 1
    )


def test_admin_when_draft_changes_after_preview_should_require_new_confirmation(
    client: Client,
) -> None:
    """Подписанный снимок не позволяет подтвердить изменённое содержимое."""
    from django.core import signing

    from apps.notifications.admin import _fingerprint

    admin = User.objects.create_superuser(
        email="admin@example.test", password="test-only"
    )
    client.force_login(admin)
    campaign = Broadcast.objects.create(
        title="Тема", body="Изначальный текст", send_inbox=True
    )
    snapshot = signing.dumps(_fingerprint(campaign), salt="notification-broadcast")
    Broadcast.objects.filter(pk=campaign.pk).update(body="Изменённый текст")
    response = client.post(
        reverse("admin:notifications_broadcast_send", args=[campaign.pk]),
        {"confirm": "yes", "snapshot": snapshot},
    )
    assert response.status_code == 400
    campaign.refresh_from_db()
    assert campaign.status == "draft"


def test_admin_when_test_sent_should_target_only_sender(
    client: Client, recipient: User
) -> None:
    """Персональная проба не использует весь список пользователей."""
    admin = User.objects.create_superuser(
        email="admin@example.test", password="test-only"
    )
    client.force_login(admin)
    original = Broadcast.objects.create(
        title="Тест", body="Пробное сообщение", send_inbox=True
    )
    response = client.post(
        reverse("admin:notifications_broadcast_test", args=[original.pk])
    )
    assert response.status_code == 302
    test = Broadcast.objects.get(is_test=True)
    expand_broadcast(test.pk)
    assert Notification.objects.filter(user=admin).exists()
    assert not Notification.objects.filter(user=recipient).exists()
    original.refresh_from_db()
    assert original.status == "draft"


def test_worker_when_push_times_out_should_reuse_key(
    recipient: User, mocker: MockerFixture
) -> None:
    """Повтор сетевого запроса использует тот же UUID провайдера."""
    from apps.notifications.infrastructure.push import PushTransportError

    NotificationPreference.objects.create(user=recipient, push_enabled=True)
    PushDevice.objects.create(
        user=recipient,
        device_hash="b" * 64,
        active=True,
        subscription_id="00000000-0000-4000-8000-000000000001",
        expires_at=timezone.now() + timedelta(days=1),
    )
    create_notification(event_key="match.created", user=recipient, message="Матч")
    send = mocker.patch(
        "apps.notifications.worker.send_push",
        side_effect=[PushTransportError("timeout"), "provider-id"],
    )
    process_deliveries(limit=1)
    Delivery.objects.update(available_at=timezone.now())
    process_deliveries(limit=1)
    assert send.call_args_list[0].kwargs["key"] == send.call_args_list[1].kwargs["key"]
    assert Delivery.objects.get().status == "accepted"


def test_worker_when_smtp_interrupted_should_not_retry_unknown_result(
    recipient: User,
) -> None:
    """После падения SMTP-worker неизвестный результат не вызывает дубль письма."""
    now = timezone.now()
    Delivery.objects.create(
        event_key="manual.broadcast",
        channel="email",
        user=recipient,
        title="Тест",
        body="Текст",
        url="https://tennisfan.ru/",
        status="processing",
        available_at=now - timedelta(minutes=10),
        expires_at=now + timedelta(days=1),
    )
    process_deliveries(limit=1)
    assert Delivery.objects.get().status == "uncertain"
    assert len(mail.outbox) == 0


def test_access_email_when_opted_out_should_still_send_required_message(
    recipient: User,
) -> None:
    """Общий отказ от email не блокирует подтверждение адреса."""
    NotificationPreference.objects.create(user=recipient, email_enabled=False)
    assert (
        EventEmail(
            event_key="account.email_verification",
            subject="Подтверждение",
            body="https://example.test/private-token",
            to=[recipient.email],
        ).send()
        == 1
    )
    assert "private-token" not in Notification.objects.get(user=recipient).message


def test_inbox_only_event_when_email_allowed_should_queue_new_email_channel(
    recipient: User,
) -> None:
    """Галочка email действует и для события, раньше существовавшего только в ЛК."""
    create_notification(
        event_key="chat.reply", user=recipient, message="Вам ответили", url="/chats/"
    )
    item = Delivery.objects.get(channel="email")
    assert item.user_id == recipient.pk
    assert item.body == "Вам ответили"


def test_password_reset_when_transport_fails_should_not_reveal_account(
    client: Client, mocker: MockerFixture
) -> None:
    """Отказ SMTP не отличает существующий аккаунт от неизвестного по HTTP-ответу."""
    User.objects.create_user(email="reset@example.test", password="test-only")
    mocker.patch(
        "apps.notifications.auth_forms.EventEmail.send",
        side_effect=OSError("smtp test"),
    )
    known = client.post(reverse("password_reset"), {"email": "reset@example.test"})
    unknown = client.post(reverse("password_reset"), {"email": "absent@example.test"})
    assert known.status_code == unknown.status_code == 302
    assert known["Location"] == unknown["Location"]


def test_generic_email_when_old_global_flag_off_should_keep_push(
    recipient: User,
) -> None:
    """Исторический выключатель почты не выключает независимый push-канал."""
    from apps.telegram_bot.notifications import _send_notification_email_to_user

    NotificationPreference.objects.create(user=recipient, push_enabled=True)
    with override_settings(USER_NOTIFICATIONS_EMAIL_ENABLED=False):
        _send_notification_email_to_user(
            recipient, "Подписка заканчивается", event_key="subscription.expiring"
        )
    assert len(mail.outbox) == 0
    assert (
        Delivery.objects.filter(
            event_key="subscription.expiring", channel="push"
        ).count()
        == 1
    )


def test_admin_when_opening_matrix_and_draft_should_render_limits(
    client: Client,
) -> None:
    """Админка действительно выводит три канала и ограниченные поля черновика."""
    admin = User.objects.create_superuser(
        email="ui-admin@example.test", password="test-only"
    )
    client.force_login(admin)
    matrix = client.get(reverse("admin:notifications_eventpolicy_changelist"))
    assert matrix.status_code == 200
    assert "Email" in matrix.content.decode()
    assert "Push" in matrix.content.decode()
    assert "ЛК" in matrix.content.decode()
    draft = client.get(reverse("admin:notifications_broadcast_add"))
    assert draft.status_code == 200
    assert 'maxlength="40"' in draft.content.decode()
    assert 'maxlength="120"' in draft.content.decode()


def test_email_when_account_inactive_should_not_treat_as_external_address(
    recipient: User,
) -> None:
    """Неактивный пользователь не превращается в неограниченного внешнего адресата."""
    recipient.is_active = False
    recipient.save()
    EventEmail(
        event_key="subscription.purchased",
        subject="Подписка",
        body="Текст",
        to=[recipient.email],
    ).send()
    assert len(mail.outbox) == 0


def test_admin_when_campaign_cancelled_should_skip_pending_deliveries(
    client: Client, recipient: User, mocker: MockerFixture
) -> None:
    """Остановка рассылки не отзывает принятые сообщения, но блокирует очередь."""
    admin = User.objects.create_superuser(
        email="cancel-admin@example.test", password="test-only"
    )
    NotificationPreference.objects.create(user=recipient, push_enabled=True)
    client.force_login(admin)
    campaign = Broadcast.objects.create(
        title="Тема", body="Текст", send_push=True, send_inbox=False
    )
    queue_broadcast(campaign.pk, actor=admin)
    expand_broadcast(campaign.pk)
    response = client.post(
        reverse("admin:notifications_broadcast_cancel", args=[campaign.pk]),
        {"confirm": "yes"},
    )
    assert response.status_code == 302
    send = mocker.patch("apps.notifications.worker.send_push")
    process_deliveries(limit=10)
    send.assert_not_called()
    assert Delivery.objects.get().status == "skipped"


@pytest.mark.parametrize(
    "model_name", ["delivery", "pushdevice", "notificationpreference"]
)
def test_audit_admin_when_staff_posts_should_not_allow_technical_edits(
    client: Client, recipient: User, model_name: str
) -> None:
    """Даже суперпользователь не может подменить согласие, адресата или статус задания."""
    admin = User.objects.create_superuser(
        email="audit-admin@example.test", password="test-only"
    )
    client.force_login(admin)
    now = timezone.now()
    preference = NotificationPreference.objects.create(user=recipient)
    device = PushDevice.objects.create(
        user=recipient, device_hash="d" * 64, expires_at=now + timedelta(days=1)
    )
    delivery = Delivery.objects.create(
        event_key="manual.broadcast",
        channel="push",
        user=recipient,
        title="Тема",
        body="Текст",
        url="https://tennisfan.ru/",
        available_at=now,
        expires_at=now + timedelta(days=1),
    )
    obj = {
        "delivery": delivery,
        "pushdevice": device,
        "notificationpreference": preference,
    }[model_name]
    response = client.post(
        reverse(f"admin:notifications_{model_name}_change", args=[obj.pk]),
        {"active": "on", "push_enabled": "on", "status": "queued"},
    )
    assert response.status_code == 403


def test_legacy_inbox_admin_when_adding_should_not_bypass_channel_policy(
    client: Client,
) -> None:
    """Старый раздел ЛК не создаёт второй неконтролируемый путь ручной рассылки."""
    admin = User.objects.create_superuser(
        email="legacy-admin@example.test", password="test-only"
    )
    client.force_login(admin)
    assert client.get(reverse("admin:users_notification_add")).status_code == 403
