"""Гостевые ссылки, запросы отмены и однократные напоминания кемпа."""

from __future__ import annotations

import re
import smtplib
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.camps.cancellations import decide_cancellation, request_cancellation
from apps.camps.guest_access import consume_guest_link, request_guest_access
from apps.camps.models import (
    CampApplication,
    CampCancellation,
    CampGuestAccess,
    CampPayment,
    CampReminder,
    CampSession,
)
from apps.camps.reminders import send_camp_reminders
from apps.camps.services import record_payment, update_application
from apps.notifications.models import Delivery, NotificationPreference
from apps.users.models import Notification, User
from tests.integration.camps.test_camps import _payload, _submit, _values

pytestmark = pytest.mark.django_db


def _link(app: CampApplication) -> str:
    """Получает секрет исключительно из тестового почтового ящика."""
    request_guest_access(email=app.email, address="127.0.0.1")
    match = re.search(r"/camps/access/([^/\s]+)/", mail.outbox[-1].body)
    assert match
    return match.group(1)


def _request(
    app: CampApplication, *, actor: User | None = None, key: uuid.UUID | None = None
) -> CampCancellation:
    """Запрашивает отмену от владельца исходной гостевой сессии."""
    return request_cancellation(
        application_id=app.pk,
        actor=actor,
        guest_application_ids=[str(app.pk)] if actor is None else [],
        submission_key=key or uuid.uuid4(),
        reason="Изменились планы",
    )


def test_camps_recovery_unknown_email_has_same_response(
    client: Client, camp_session: CampSession
) -> None:
    """Внешний ответ не раскрывает наличие заявки по email."""
    app = _submit(camp_session)
    existing = client.post(reverse("camps:recover"), {"email": app.email})
    missing = client.post(reverse("camps:recover"), {"email": "missing@example.test"})
    assert existing.status_code == missing.status_code == 200
    assert "Если на этот адрес есть гостевые заявки" in existing.content.decode()
    assert "Если на этот адрес есть гостевые заявки" in missing.content.decode()
    assert len(mail.outbox) == 1


def test_camps_recovery_get_does_not_consume_link(
    client: Client, camp_session: CampSession
) -> None:
    """Почтовый сканер не расходует ссылку и не получает права по одному GET."""
    app = _submit(camp_session)
    token = _link(app)
    response = client.get(reverse("camps:restore", args=[token]))
    assert response.status_code == 200
    assert response["Referrer-Policy"] == "no-referrer"
    assert "no-store" in response["Cache-Control"]
    assert "mc.yandex.ru" not in response.content.decode()
    assert CampGuestAccess.objects.get().used_at is None
    assert client.get(reverse("camps:application", args=[app.pk])).status_code == 404


def test_camps_recovery_post_grants_guest_requests_without_claiming_account(
    client: Client, camp_session: CampSession
) -> None:
    """Email-подтверждение даёт сессионный доступ без изменения владельца заявки."""
    app = _submit(camp_session)
    user = User.objects.create_user(email=app.email, password="test-only")
    token = _link(app)
    response = client.post(reverse("camps:restore", args=[token]))
    assert response.status_code == 302
    assert client.get(reverse("camps:application", args=[app.pk])).status_code == 200
    assert str(app.pk) in client.session["camp_application_ids"]
    assert "_auth_user_id" not in client.session
    app.refresh_from_db()
    assert app.user_id is None
    account = Client()
    account.force_login(user)
    assert account.get(reverse("camps:application", args=[app.pk])).status_code == 404
    assert token != CampGuestAccess.objects.get().token_hash


def test_camps_recovery_only_original_guest_snapshot(
    camp_session: CampSession, manager: User
) -> None:
    """Ссылка не охватывает аккаунтные или поданные позже заявки."""
    app = _submit(camp_session)
    account_app = _submit(camp_session, user=manager)
    token = _link(app)
    late = _submit(camp_session)
    assert consume_guest_link(token) == [str(app.pk)]
    assert account_app.user_id == manager.pk
    assert late.pk != app.pk


def test_camps_recovery_expired_replayed_and_invalid_links(
    client: Client, camp_session: CampSession
) -> None:
    """Истёкшая, повторная и поддельная ссылки не дают доступ."""
    app = _submit(camp_session)
    token = _link(app)
    CampGuestAccess.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert (
        "Ссылка больше не действует"
        in client.post(reverse("camps:restore", args=[token])).content.decode()
    )
    with pytest.raises(ValidationError):
        consume_guest_link("fake-token")
    CampGuestAccess.objects.update(expires_at=timezone.now() + timedelta(minutes=10))
    consume_guest_link(token)
    with pytest.raises(ValidationError):
        consume_guest_link(token)


def test_camps_recovery_token_post_requires_csrf(camp_session: CampSession) -> None:
    """Сторонний сайт не может подтвердить гостевой доступ чужому браузеру."""
    app = _submit(camp_session)
    token = _link(app)
    client = Client(enforce_csrf_checks=True)
    assert client.post(reverse("camps:restore", args=[token])).status_code == 403
    assert CampGuestAccess.objects.get().used_at is None


def test_camps_recovery_rate_limits_email(camp_session: CampSession) -> None:
    """Запросы с разных устройств не обходят ограничение писем на один адрес."""
    app = _submit(camp_session)
    for index in range(5):
        request_guest_access(email=app.email.upper(), address=f"127.0.0.{index}")
    assert len(mail.outbox) == 2
    assert CampGuestAccess.objects.count() == 2


def test_camps_access_email_ignores_marketing_opt_out_without_secret_in_inbox(
    camp_session: CampSession,
) -> None:
    """Запрошенная ссылка не зависит от подписки и не попадает в уведомления аккаунта."""
    app = _submit(camp_session)
    user = User.objects.create_user(email=app.email, password="test-only")
    NotificationPreference.objects.create(user=user, email_enabled=False)
    token = _link(app)
    assert token in mail.outbox[0].body
    assert not Notification.objects.filter(user=user).exists()
    assert not Delivery.objects.filter(user=user).exists()


def test_camps_cancellation_request_preserves_place_and_money(
    camp_session: CampSession, manager: User
) -> None:
    """Обращение участника не отменяет подтверждение и не создаёт возврат."""
    app = _submit(camp_session)
    update_application(application_id=app.pk, actor=manager, values=_values())
    record_payment(
        application_id=app.pk,
        actor=manager,
        amount=Decimal("5000"),
        reference="Взнос",
        operation_key=uuid.uuid4(),
    )
    request = _request(app)
    app.refresh_from_db()
    assert app.status == "confirmed"
    assert camp_session.confirmed_players == 1
    assert request.status == "pending"
    assert app.paid_amount == Decimal("5000")
    assert CampPayment.objects.count() == 1


def test_camps_cancellation_request_idempotent(camp_session: CampSession) -> None:
    """Повторные нажатия и новая форма не дублируют открытый запрос."""
    app = _submit(camp_session)
    key = uuid.uuid4()
    first = _request(app, key=key)
    assert _request(app, key=key).pk == first.pk
    assert _request(app).pk == first.pk
    assert CampCancellation.objects.count() == 1


def test_camps_cancellation_owner_and_staff_permissions(
    client: Client, camp_session: CampSession, manager: User
) -> None:
    """Чужой участник не отменяет заявку, обычный игрок не принимает решение."""
    app = _submit(camp_session)
    with pytest.raises(PermissionDenied):
        request_cancellation(
            application_id=app.pk,
            actor=None,
            guest_application_ids=[],
            submission_key=uuid.uuid4(),
            reason="Чужой запрос",
        )
    assert client.get(reverse("camps:cancel", args=[app.pk])).status_code == 404
    request = _request(app)
    user = User.objects.create_user(email="outsider@example.test", password="test-only")
    with pytest.raises(PermissionDenied):
        decide_cancellation(
            request_id=request.pk,
            application_id=app.pk,
            actor=user,
            decision="approved",
            resolution="Отмена",
        )


def test_camps_cancellation_approve_releases_place_without_refund(
    camp_session: CampSession, manager: User
) -> None:
    """Одобрение меняет статус участия и освобождает место без денежной проводки."""
    app = _submit(camp_session)
    update_application(application_id=app.pk, actor=manager, values=_values())
    request = _request(app)
    for _ in range(2):
        decide_cancellation(
            request_id=request.pk,
            application_id=app.pk,
            actor=manager,
            decision="approved",
            resolution="Отмена подтверждена, свяжемся по возврату.",
        )
    app.refresh_from_db()
    request.refresh_from_db()
    assert app.status == "cancelled"
    assert request.status == "approved"
    assert request.resolved_by_id == manager.pk
    assert camp_session.confirmed_players == 0
    assert not CampPayment.objects.exists()


def test_camps_cancellation_reject_keeps_place_and_allows_new_request(
    camp_session: CampSession, manager: User
) -> None:
    """Отклонение сохраняет участие, историю и возможность нового обращения."""
    app = _submit(camp_session)
    update_application(application_id=app.pk, actor=manager, values=_values())
    request = _request(app)
    decide_cancellation(
        request_id=request.pk,
        application_id=app.pk,
        actor=manager,
        decision="rejected",
        resolution="Обсудим частичную замену участника.",
    )
    app.refresh_from_db()
    assert app.status == "confirmed"
    assert camp_session.confirmed_players == 1
    assert _request(app).pk != request.pk


def test_camps_direct_admin_cancellation_closes_request(
    camp_session: CampSession, manager: User
) -> None:
    """Ручная смена статуса не оставляет висящий запрос на отмену."""
    app = _submit(camp_session)
    request = _request(app)
    update_application(
        application_id=app.pk, actor=manager, values=_values("cancelled")
    )
    request.refresh_from_db()
    assert request.status == "approved"


def test_camps_cancellation_form_and_decision_flow(
    client: Client, camp_session: CampSession, manager: User
) -> None:
    """Гость подаёт запрос, а команда решает его через рабочий экран."""
    client.post(
        reverse("camps:apply", args=[camp_session.pk]), _payload(client, camp_session)
    )
    app = CampApplication.objects.get()
    url = reverse("camps:cancel", args=[app.pk])
    client.get(url)
    response = client.post(
        url,
        {
            "submission_key": client.session[f"camp_cancel_key_{app.pk}"],
            "reason": "Не могу поехать",
            "confirm": "on",
        },
    )
    assert response.status_code == 302
    assert (
        "Ожидает решения"
        in client.get(reverse("camps:application", args=[app.pk])).content.decode()
    )
    request = app.cancellation_requests.get()
    team = Client()
    team.force_login(manager)
    url = reverse("camps:manage_application", args=[app.pk])
    assert "Не могу поехать" in team.get(url).content.decode()
    response = team.post(
        url,
        {
            "action": "cancellation",
            "request_id": request.pk,
            "decision": "approved",
            "resolution": "Отмена подтверждена",
        },
    )
    assert response.status_code == 302
    app.refresh_from_db()
    assert app.status == "cancelled"


def test_camps_cancellation_notifies_team_and_applicant(
    camp_session: CampSession, manager: User, django_capture_on_commit_callbacks: Any
) -> None:
    """Запрос создаёт событие координатору и подтверждение гостю после commit."""
    app = _submit(camp_session)
    with django_capture_on_commit_callbacks(execute=True):
        _request(app)
    assert (
        Delivery.objects.filter(
            user=manager, event_key="camp.cancellation_requested", channel="email"
        ).count()
        == 1
    )
    assert len(mail.outbox) == 1


def test_camps_departure_reminder_once_and_no_side_effects_on_dry_run(
    camp_session: CampSession, manager: User
) -> None:
    """Часовые повторы cron не повторяют одно напоминание о заезде."""
    app = _submit(camp_session)
    update_application(application_id=app.pk, actor=manager, values=_values())
    camp_session.start_date = timezone.localdate() + timedelta(days=7)
    camp_session.save()
    call_command("send_camp_reminders", dry_run=True)
    assert not CampReminder.objects.exists()
    assert not mail.outbox
    assert send_camp_reminders() == 1
    assert send_camp_reminders() == 0
    assert len(mail.outbox) == 1
    assert app.reminders.get().kind == "departure_7"


def test_camps_payment_reminder_stops_after_payment(
    camp_session: CampSession, manager: User
) -> None:
    """Доплата учитывает фактический остаток и перестаёт напоминать после оплаты."""
    app = _submit(camp_session)
    update_application(
        application_id=app.pk,
        actor=manager,
        values=_values(payment_due=timezone.localdate() + timedelta(days=1)),
    )
    assert send_camp_reminders() == 1
    record_payment(
        application_id=app.pk,
        actor=manager,
        amount=Decimal("45000"),
        reference="Полная оплата",
        operation_key=uuid.uuid4(),
    )
    app.payment_due = timezone.localdate()
    app.save(update_fields=["payment_due"])
    assert send_camp_reminders() == 0
    assert len(mail.outbox) == 1


def test_camps_pending_cancellation_suppresses_reminders(
    camp_session: CampSession, manager: User
) -> None:
    """Во время рассмотрения отмены не просим доплатить за поездку."""
    app = _submit(camp_session)
    update_application(
        application_id=app.pk,
        actor=manager,
        values=_values(payment_due=timezone.localdate() + timedelta(days=1)),
    )
    _request(app)
    assert send_camp_reminders() == 0
    assert not CampReminder.objects.exists()


def test_camps_unanswered_reminder_goes_to_team_once(
    camp_session: CampSession, manager: User
) -> None:
    """Через сутки после новой заявки напоминание получает команда, а не участник."""
    app = _submit(camp_session)
    CampApplication.objects.filter(pk=app.pk).update(
        created_at=timezone.now() - timedelta(hours=25)
    )
    assert send_camp_reminders() == 1
    assert send_camp_reminders() == 0
    assert (
        Delivery.objects.filter(
            user=manager, event_key="camp.unanswered_reminder"
        ).count()
        == 1
    )
    assert not mail.outbox


def test_camps_cancelled_session_and_application_have_no_reminders(
    camp_session: CampSession, manager: User
) -> None:
    """Закрытые поездки и заявки исключены из плановой доставки."""
    app = _submit(camp_session)
    update_application(
        application_id=app.pk,
        actor=manager,
        values=_values(payment_due=timezone.localdate()),
    )
    update_application(
        application_id=app.pk, actor=manager, values=_values("cancelled")
    )
    assert send_camp_reminders() == 0


def test_camps_registered_reminders_use_notification_queue(
    camp_session: CampSession, manager: User
) -> None:
    """У аккаунта email/ЛК проходят существующую очередь и согласия."""
    app = _submit(camp_session, user=manager)
    update_application(
        application_id=app.pk,
        actor=manager,
        values=_values(payment_due=timezone.localdate()),
    )
    assert send_camp_reminders() == 1
    assert (
        Delivery.objects.filter(
            user=manager, channel="email", event_key="camp.payment_reminder"
        ).count()
        == 1
    )
    assert (
        Notification.objects.filter(
            user=manager, event_key="camp.payment_reminder"
        ).count()
        == 1
    )
    assert not mail.outbox


def test_camps_reminder_smtp_rejection_retries_without_duplicate_success(
    camp_session: CampSession, manager: User
) -> None:
    """Явный отказ SMTP повторяется после паузы, успешная доставка не повторяется."""
    app = _submit(camp_session)
    update_application(
        application_id=app.pk,
        actor=manager,
        values=_values(payment_due=timezone.localdate()),
    )
    with patch(
        "apps.camps.reminders.send_camp_event",
        side_effect=smtplib.SMTPDataError(451, b"try later"),
    ):
        assert send_camp_reminders() == 0
    row = CampReminder.objects.get()
    assert row.status == "failed"
    assert send_camp_reminders() == 0
    CampReminder.objects.update(updated_at=timezone.now() - timedelta(hours=2))
    assert send_camp_reminders() == 1
    assert send_camp_reminders() == 0
    assert len(mail.outbox) == 1


def test_camps_reminder_unknown_smtp_result_is_not_blindly_repeated(
    camp_session: CampSession, manager: User
) -> None:
    """Сетевой обрыв после возможного приёма письма помечается неопределённым."""
    app = _submit(camp_session)
    update_application(
        application_id=app.pk,
        actor=manager,
        values=_values(payment_due=timezone.localdate()),
    )
    with patch("apps.camps.reminders.send_camp_event", side_effect=TimeoutError):
        assert send_camp_reminders() == 0
    assert CampReminder.objects.get().status == "uncertain"
    assert send_camp_reminders() == 0


def test_camps_recovery_guest_pages_and_cancel_post_require_owner(
    client: Client,
) -> None:
    """Публичная точка восстановления не раскрывает чужую заявку по UUID."""
    assert client.get(reverse("camps:my")).status_code == 200
    assert (
        client.post(
            reverse("camps:cancel", args=[uuid.uuid4()]), {"reason": "Нет"}
        ).status_code
        == 404
    )


def test_camps_access_link_not_stored_in_outbound_log(
    camp_session: CampSession, settings: Any
) -> None:
    """Почтовый журнал не сохраняет секрет одноразового доступа."""
    from apps.core.models import OutboundEmail

    settings.EMAIL_BACKEND = "apps.core.mail.LoggingEmailBackend"
    settings.EMAIL_BACKEND_INNER = "django.core.mail.backends.locmem.EmailBackend"
    app = _submit(camp_session)
    token = _link(app)
    logged = OutboundEmail.objects.get()
    assert token not in logged.body_text
    assert token not in logged.body_html
    assert "скрыта" in logged.body_text


def test_camps_old_failed_reminder_is_superseded_by_nearer_deadline(
    camp_session: CampSession, manager: User
) -> None:
    """Не отправляет старое и новое напоминание о той же доплате за один проход."""
    app = _submit(camp_session)
    due = timezone.localdate() + timedelta(days=1)
    update_application(
        application_id=app.pk, actor=manager, values=_values(payment_due=due)
    )
    old = CampReminder.objects.create(
        application=app,
        kind="payment_3",
        basis=due.isoformat(),
        status="failed",
        attempts=1,
        updated_at=timezone.now() - timedelta(hours=2),
    )
    assert send_camp_reminders() == 1
    old.refresh_from_db()
    assert old.status == "skipped"
    assert len(mail.outbox) == 1


def test_camps_reminder_rechecks_unpaid_balance_before_retry(
    camp_session: CampSession, manager: User
) -> None:
    """Оплаченная заявка не получает ранее отложенное напоминание."""
    app = _submit(camp_session)
    due = timezone.localdate()
    update_application(
        application_id=app.pk, actor=manager, values=_values(payment_due=due)
    )
    row = CampReminder.objects.create(
        application=app,
        kind="payment_0",
        basis=due.isoformat(),
        status="failed",
        attempts=1,
        updated_at=timezone.now() - timedelta(hours=2),
    )
    record_payment(
        application_id=app.pk,
        actor=manager,
        amount=Decimal("45000"),
        reference="Оплата",
        operation_key=uuid.uuid4(),
    )
    assert send_camp_reminders() == 0
    row.refresh_from_db()
    assert row.status == "skipped"


def test_camps_reminder_policy_opt_out_marks_skipped(
    camp_session: CampSession, manager: User
) -> None:
    """Отключённый почтовый канал не вызывает бесконечных повторов гостю."""
    from apps.notifications.models import EventPolicy

    app = _submit(camp_session)
    update_application(
        application_id=app.pk,
        actor=manager,
        values=_values(payment_due=timezone.localdate()),
    )
    EventPolicy.objects.filter(key="camp.payment_reminder").update(email_enabled=False)
    assert send_camp_reminders() == 0
    assert CampReminder.objects.get().status == "skipped"
    assert not mail.outbox


def test_camps_pending_cancellation_cannot_be_confirmed(
    camp_session: CampSession, manager: User
) -> None:
    """Команда не подтверждает поездку до решения по уже поданному запросу отмены."""
    app = _submit(camp_session)
    _request(app)
    with pytest.raises(ValidationError, match="отмену"):
        update_application(application_id=app.pk, actor=manager, values=_values())


@pytest.mark.parametrize(
    "days,kind",
    [(3, "payment_3"), (1, "payment_1"), (0, "payment_0"), (-1, "payment_overdue")],
)
def test_camps_payment_windows_and_overdue_are_once(
    camp_session: CampSession, manager: User, days: int, kind: str
) -> None:
    """Каждое календарное окно оплаты создаёт один повод, а не ежедневную рассылку."""
    app = _submit(camp_session)
    update_application(
        application_id=app.pk,
        actor=manager,
        values=_values(payment_due=timezone.localdate() + timedelta(days=days)),
    )
    assert send_camp_reminders() == 1
    assert send_camp_reminders() == 0
    assert app.reminders.get().kind == kind


def test_camps_pending_requests_are_visible_in_session_filter(
    client: Client, camp_session: CampSession, manager: User
) -> None:
    """Координатор видит ожидающие запросы непосредственно в списке заезда."""
    app = _submit(camp_session)
    _request(app)
    _submit(camp_session)
    client.force_login(manager)
    response = client.get(
        reverse("camps:manage_session", args=[camp_session.pk]),
        {"cancellation": "pending"},
    )
    assert response.status_code == 200
    assert len(response.context["applications"]) == 1
    assert "Запрос на отмену" in response.content.decode()


def test_camps_guest_receipt_contains_recovery_entry(
    camp_session: CampSession, django_capture_on_commit_callbacks: Any
) -> None:
    """Первое письмо подсказывает гостю, как вернуться с другого устройства."""
    with django_capture_on_commit_callbacks(execute=True):
        _submit(camp_session)
    assert "/camps/recover/" in mail.outbox[0].body


def test_camps_cancellation_discards_queued_reminders(
    camp_session: CampSession, manager: User
) -> None:
    """Запрос отмены снимает ещё не отправленное напоминание из общей очереди."""
    app = _submit(camp_session, user=manager)
    update_application(
        application_id=app.pk,
        actor=manager,
        values=_values(payment_due=timezone.localdate()),
    )
    send_camp_reminders()
    assert Delivery.objects.filter(
        event_key="camp.payment_reminder", status="queued"
    ).exists()
    _request(app, actor=manager)
    assert not Delivery.objects.filter(
        event_key="camp.payment_reminder", status="queued"
    ).exists()
    assert Delivery.objects.filter(
        event_key="camp.payment_reminder", status="skipped"
    ).exists()


def test_camps_full_payment_discards_queued_payment_reminder(
    camp_session: CampSession, manager: User
) -> None:
    """Внесённая до отправки письма полная оплата убирает устаревшее требование доплаты."""
    app = _submit(camp_session, user=manager)
    update_application(
        application_id=app.pk,
        actor=manager,
        values=_values(payment_due=timezone.localdate()),
    )
    send_camp_reminders()
    record_payment(
        application_id=app.pk,
        actor=manager,
        amount=Decimal("45000"),
        reference="Полная оплата",
        operation_key=uuid.uuid4(),
    )
    assert not Delivery.objects.filter(
        event_key="camp.payment_reminder", status="queued"
    ).exists()
