"""Бронь до кемпа, три напоминания и защищённый ответ о приезде."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.camps.attendance import (
    attendance_token,
    confirm_attendance,
    guest_attendance_application,
)
from apps.camps.models import CampPayment, CampSession
from apps.camps.reminders import send_camp_reminders
from apps.camps.reservations import reservation_limit
from apps.camps.services import update_application
from apps.notifications.models import Delivery, NotificationPreference
from apps.users.models import Notification, User
from tests.integration.camps.test_camp_followup import _request
from tests.integration.camps.test_camps import _submit, _values

pytestmark = pytest.mark.django_db


def _upcoming(session: CampSession, days: int = 7) -> None:
    """Переносит заезд для проверки календарных напоминаний."""
    session.start_date = timezone.localdate() + timedelta(days=days)
    session.save()


def test_camps_three_guest_reminders_include_html_confirmation(
    camp_session: CampSession,
) -> None:
    """За 7, 3 и 1 день приходит отдельное письмо с кнопкой без повторов cron."""
    app = _submit(camp_session)
    _upcoming(camp_session)
    app.refresh_from_db()
    assert app.reservation_expires_at == reservation_limit(camp_session)
    for days in [7, 3, 1]:
        with patch(
            "django.utils.timezone.localdate",
            return_value=camp_session.start_date - timedelta(days=days),
        ):
            assert send_camp_reminders() == 1
            assert send_camp_reminders() == 0
        message = mail.outbox[-1]
        assert "подтвердите" in message.body
        assert "/camps/confirm-trip/" in message.body
        assert "Подтвердить поездку" in message.alternatives[0].content
        assert message.sensitive_body
    assert len(mail.outbox) == 3
    assert set(app.reminders.values_list("kind", flat=True)) == {
        "departure_7",
        "departure_3",
        "departure_1",
    }
    assert not CampPayment.objects.exists()


def test_camps_guest_get_is_read_only_post_is_idempotent(
    client: Client, camp_session: CampSession, django_capture_on_commit_callbacks: Any
) -> None:
    """Сканер письма ничего не подтверждает; явный ответ не открывает частную заявку."""
    app = _submit(camp_session)
    token = attendance_token(app)
    url = reverse("camps:attendance_guest", args=[token])
    assert client.get(url).status_code == 200
    app.refresh_from_db()
    assert not app.attendance_confirmed
    with patch("apps.camps.attendance.send_camp_event") as send:
        with django_capture_on_commit_callbacks(execute=True):
            first = client.post(url)
            second = client.post(url)
        assert send.call_count == 1
    assert first.status_code == second.status_code == 200
    assert "Ждём вас на кемпе" in second.content.decode()
    app.refresh_from_db()
    assert app.attendance_confirmed and app.status == "new"
    assert app.quoted_total is None and app.paid_amount == 0
    assert app.has_active_reservation
    assert client.get(reverse("camps:application", args=[app.pk])).status_code == 404
    assert "_auth_user_id" not in client.session
    assert "camp_application_ids" not in client.session
    assert first["Referrer-Policy"] == "no-referrer"
    assert "no-store" in first["Cache-Control"]
    assert "mc.yandex.ru" not in first.content.decode()


def test_camps_guest_confirmation_requires_csrf(camp_session: CampSession) -> None:
    """Чужой сайт не может отправить подтверждение вместо гостя."""
    app = _submit(camp_session)
    client = Client(enforce_csrf_checks=True)
    url = reverse("camps:attendance_guest", args=[attendance_token(app)])
    assert client.post(url).status_code == 403
    client.get(url)
    response = client.post(
        url, {"csrfmiddlewaretoken": client.cookies["csrftoken"].value}
    )
    assert response.status_code == 200
    app.refresh_from_db()
    assert app.attendance_confirmed


@pytest.mark.parametrize(
    "change",
    ["email", "date", "cancelled", "waitlist", "pending", "expired", "tampered", "age"],
)
def test_camps_guest_link_rejects_obsolete_conditions(
    camp_session: CampSession, manager: User, change: str
) -> None:
    """Подпись не обходит срок, изменения условий и права на спортивное место."""
    app = _submit(camp_session)
    token = attendance_token(app)
    if change == "email":
        app.email = "another@example.test"
        app.save()
    elif change == "date":
        _upcoming(camp_session)
    elif change in {"cancelled", "waitlist"}:
        update_application(application_id=app.pk, actor=manager, values=_values(change))
    elif change == "pending":
        _request(app)
    elif change == "expired":
        app.reservation_expires_at = timezone.now() - timedelta(seconds=1)
        app.save(update_fields=["reservation_expires_at"])
    elif change == "tampered":
        token += "x"
    elif change == "age":
        with patch(
            "django.core.signing.time.time",
            return_value=timezone.now().timestamp() + 9 * 24 * 60 * 60,
        ):
            assert guest_attendance_application(token) is None
        return
    assert guest_attendance_application(token) is None


def test_camps_account_gets_all_channels_without_secret_link(
    camp_session: CampSession,
) -> None:
    """Для аккаунта создаются ЛК, push и email с обычной ссылкой на свою заявку."""
    user = User.objects.create_user(email="attendee@example.test", password="test")
    NotificationPreference.objects.create(
        user=user, push_enabled=True, email_enabled=True
    )
    app = _submit(camp_session, user=user)
    _upcoming(camp_session)
    assert send_camp_reminders() == 1
    note = Notification.objects.get(user=user, event_key="camp.departure_reminder")
    assert note.is_visible and note.url == reverse("camps:application", args=[app.pk])
    assert set(
        Delivery.objects.filter(notification=note).values_list("channel", flat=True)
    ) == {"email", "push"}
    assert not mail.outbox
    with pytest.raises(ValidationError):
        attendance_token(app)


def test_camps_account_can_confirm_only_own_application(
    client: Client, camp_session: CampSession
) -> None:
    """Чужая заявка недоступна через POST, владелец подтверждает из ЛК."""
    user = User.objects.create_user(email="attendee@example.test", password="test")
    other = User.objects.create_user(email="other@example.test", password="test")
    app = _submit(camp_session, user=user)
    with pytest.raises(PermissionDenied):
        confirm_attendance(application_id=app.pk, actor=other)
    client.force_login(other)
    url = reverse("camps:attendance", args=[app.pk])
    assert client.post(url).status_code == 404
    client.force_login(user)
    assert client.get(url).status_code == 405
    assert client.post(url).status_code == 302
    app.refresh_from_db()
    assert app.attendance_confirmed and app.status == "new"


def test_camps_confirmation_changes_remaining_reminders(
    camp_session: CampSession,
) -> None:
    """Один ответ достаточен, но дальнейшие напоминания о поездке продолжаются."""
    app = _submit(camp_session)
    _upcoming(camp_session)
    app.refresh_from_db()
    assert send_camp_reminders() == 1
    confirm_attendance(application_id=app.pk, token=attendance_token(app))
    for days in [3, 1]:
        with patch(
            "django.utils.timezone.localdate",
            return_value=camp_session.start_date - timedelta(days=days),
        ):
            assert send_camp_reminders() == 1
        assert "Вы уже подтвердили поездку" in mail.outbox[-1].body
        assert "Пожалуйста, подтвердите" not in mail.outbox[-1].body


def test_camps_new_start_requires_new_confirmation(camp_session: CampSession) -> None:
    """Перенос начала сохраняет место, но старый ответ не выдаётся за согласие на новую дату."""
    app = _submit(camp_session)
    old = attendance_token(app)
    confirm_attendance(application_id=app.pk, token=old)
    _upcoming(camp_session, days=20)
    app.refresh_from_db()
    assert not app.attendance_confirmed and app.has_active_reservation
    assert app.reservation_expires_at == reservation_limit(camp_session)
    assert guest_attendance_application(old) is None
    confirm_attendance(application_id=app.pk, token=attendance_token(app))
    app.refresh_from_db()
    assert app.attendance_confirmed


def test_camps_confirmation_hides_queued_outdated_requests(
    camp_session: CampSession,
) -> None:
    """Уже ответивший участник не получает отложенную старую просьбу подтвердить."""
    user = User.objects.create_user(email="attendee@example.test", password="test")
    NotificationPreference.objects.create(
        user=user, push_enabled=True, email_enabled=True
    )
    app = _submit(camp_session, user=user)
    _upcoming(camp_session)
    send_camp_reminders()
    confirm_attendance(application_id=app.pk, actor=user)
    assert not Delivery.objects.filter(
        user=user, event_key="camp.departure_reminder", status="queued"
    ).exists()
    assert not Notification.objects.filter(
        user=user, event_key="camp.departure_reminder", is_visible=True
    ).exists()


def test_camps_waitlist_gets_no_confirmation_requests(
    camp_session: CampSession,
) -> None:
    """Лист ожидания не создаёт ложное впечатление наличия мест."""
    _submit(camp_session, count=2)
    waiting = _submit(camp_session)
    _upcoming(camp_session)
    assert send_camp_reminders() == 1
    assert not waiting.reminders.exists()
    assert guest_attendance_application(attendance_token(waiting)) is None


def test_camps_guest_mail_with_matching_account_keeps_secret_out_of_inbox(
    camp_session: CampSession,
) -> None:
    """Совпадение email не передаёт гостевую ссылку в аккаунт или push."""
    app = _submit(camp_session)
    user = User.objects.create_user(email=app.email, password="test")
    NotificationPreference.objects.create(
        user=user, push_enabled=True, email_enabled=True
    )
    _upcoming(camp_session)
    assert send_camp_reminders() == 1
    assert "/camps/confirm-trip/" in mail.outbox[-1].body
    assert not Notification.objects.filter(user=user).exists()
    assert not Delivery.objects.filter(user=user).exists()


def test_camps_reschedule_skips_queued_old_departure(camp_session: CampSession) -> None:
    """Перенос не оставляет в очереди напоминание с устаревшей датой."""
    user = User.objects.create_user(email="attendee@example.test", password="test")
    NotificationPreference.objects.create(
        user=user, push_enabled=True, email_enabled=True
    )
    app = _submit(camp_session, user=user)
    _upcoming(camp_session)
    send_camp_reminders()
    assert Delivery.objects.filter(user=user, status="queued").count() == 2
    _upcoming(camp_session, days=10)
    assert not Delivery.objects.filter(user=user, status="queued").exists()
    app.refresh_from_db()
    assert app.has_active_reservation


def test_camps_guest_confirmation_secret_is_not_logged(
    camp_session: CampSession, settings: Any
) -> None:
    """Журнал писем сохраняет факт доставки без токена в тексте и HTML."""
    from apps.core.models import OutboundEmail

    settings.EMAIL_BACKEND = "apps.core.mail.LoggingEmailBackend"
    settings.EMAIL_BACKEND_INNER = "django.core.mail.backends.locmem.EmailBackend"
    _submit(camp_session)
    _upcoming(camp_session)
    assert send_camp_reminders() == 1
    assert "/camps/confirm-trip/" in mail.outbox[-1].body
    record = OutboundEmail.objects.get()
    assert "/camps/confirm-trip/" not in record.body_text
    assert not record.body_html
