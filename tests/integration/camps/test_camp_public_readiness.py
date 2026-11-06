"""Публичный сценарий заявки: готовность заезда, CTA и снимок дат."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.camps.cancellations import decide_cancellation, request_cancellation
from apps.camps.communication import team_recipients
from apps.camps.models import CampApplication, CampReminder, CampSession
from apps.camps.reminders import send_camp_reminders
from apps.camps.services import update_application
from apps.users.models import User
from tests.integration.camps.test_camps import _payload, _submit, _values

pytestmark = pytest.mark.django_db


def _request_cancellation(app):
    """Запрашивает отмену от владельца гостевой заявки."""
    return request_cancellation(
        application_id=app.pk,
        actor=None,
        guest_application_ids=[str(app.pk)],
        submission_key=uuid.uuid4(),
        reason="Изменились планы",
    )


def test_camps_public_page_offers_contact_without_packages(
    client: Client, camp_session: CampSession
) -> None:
    """Заезд без пакетов предлагает связаться с координатором, а не тупик."""
    camp_session.packages.all().delete()
    camp_session.contact_email = "camp@example.test"
    camp_session.contact_phone = "+79990000000"
    camp_session.save(update_fields=["contact_email", "contact_phone"])

    body = client.get(camp_session.camp.get_absolute_url()).content.decode()

    assert "Связаться с координатором" in body
    assert "mailto:camp@example.test" in body
    assert "Пакеты участия ещё не опубликованы" in body
    assert f"/camps/sessions/{camp_session.pk}/apply/" not in body


def test_camps_public_page_offers_contact_when_all_packages_inactive(
    client: Client, camp_session: CampSession
) -> None:
    """Неактивные пакеты не оставляют блок дат молчаливым."""
    camp_session.packages.update(is_active=False)
    camp_session.contact_email = "camp@example.test"
    camp_session.save(update_fields=["contact_email"])

    body = client.get(camp_session.camp.get_absolute_url()).content.decode()

    assert "Связаться с координатором" in body
    assert "Пакеты участия ещё не опубликованы" in body
    assert "Уточняем пакеты участия" not in body


def test_camps_public_list_links_to_application(
    client: Client, camp_session: CampSession
) -> None:
    """Каталог ведёт сразу к форме заявки, если заезд готов."""
    body = client.get(reverse("camps:list")).content.decode()

    assert f"/camps/sessions/{camp_session.pk}/apply/" in body


def test_camps_apply_redirect_explains_missing_packages(
    client: Client, camp_session: CampSession
) -> None:
    """Прямой адрес формы объясняет отсутствие пакетов, а не закрытый набор."""
    camp_session.packages.all().delete()

    response = client.get(reverse("camps:apply", args=[camp_session.pk]), follow=True)
    body = response.content.decode()

    assert "Пакеты участия ещё не опубликованы" in body
    assert "Приём заявок на этот заезд закрыт" not in body


def test_camps_apply_redirect_explains_expired_deadline(
    client: Client, camp_session: CampSession
) -> None:
    """Истёкший дедлайн называется своей причиной."""
    camp_session.registration_deadline = timezone.localdate() - timedelta(days=1)
    camp_session.save(update_fields=["registration_deadline"])

    response = client.get(reverse("camps:apply", args=[camp_session.pk]), follow=True)
    body = response.content.decode()

    assert "Срок приёма заявок истёк" in body


def test_camps_open_session_requires_active_package(
    camp_session: CampSession,
) -> None:
    """Открытый заезд нельзя сохранить без активного пакета."""
    camp_session.packages.update(is_active=False)

    with pytest.raises(ValidationError):
        camp_session.full_clean()


def test_camps_rejected_cancellation_restores_pending_reminder(
    camp_session: CampSession, manager: User
) -> None:
    """Пока отмена рассматривается, напоминание не сжигается, а после отказа уходит."""
    app = _submit(camp_session)
    update_application(
        application_id=app.pk,
        actor=manager,
        values=_values(payment_due=timezone.localdate() + timedelta(days=1)),
    )
    app.refresh_from_db()
    row = CampReminder.objects.create(
        application=app,
        kind="payment_1",
        basis=app.payment_due.isoformat(),
        status="pending",
    )
    request = _request_cancellation(app)

    assert send_camp_reminders() == 0
    row.refresh_from_db()
    assert row.status == "pending"

    decide_cancellation(
        request_id=request.pk,
        application_id=app.pk,
        actor=manager,
        decision="rejected",
        resolution="Оставляем участие в силе.",
    )
    assert send_camp_reminders() == 1
    row.refresh_from_db()
    assert row.status == "sent"
    assert len(mail.outbox) == 1


def test_camps_team_recipients_falls_back_from_inactive_coordinator(
    camp_session: CampSession, manager: User
) -> None:
    """Неактивный координатор не оставляет заявку без уведомления команде."""
    coordinator = User.objects.create_user(
        email="inactive-coordinator@example.test",
        password="test-only",
        is_staff=True,
        is_active=False,
    )
    camp_session.coordinator = coordinator
    camp_session.save(update_fields=["coordinator"])
    app = _submit(camp_session)

    assert manager in team_recipients(app)


def test_camps_application_keeps_snapshot_dates_after_reschedule(
    client: Client, camp_session: CampSession
) -> None:
    """Страница заявки хранит даты подачи и отдельно сообщает о переносе."""
    client.post(
        reverse("camps:apply", args=[camp_session.pk]), _payload(client, camp_session)
    )
    app = CampApplication.objects.get()
    old_start = camp_session.start_date
    camp_session.start_date = old_start + timedelta(days=10)
    camp_session.end_date = camp_session.end_date + timedelta(days=10)
    camp_session.save()

    body = client.get(reverse("camps:application", args=[app.pk])).content.decode()

    assert old_start.strftime("%d.%m.%Y") in body
    assert "Даты заезда изменены координатором" in body
