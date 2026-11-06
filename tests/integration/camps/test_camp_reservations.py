"""Временная бронь всей группы, освобождение мест и работа координатора."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.camps.cancellations import decide_cancellation
from apps.camps.models import CampPayment, CampSession
from apps.camps.reservations import (
    default_reservation_deadline,
    expire_reservations,
    reservation_limit,
    reserve_application,
)
from apps.camps.services import submit_application, update_application
from apps.users.models import User
from tests.integration.camps.test_camp_followup import _request
from tests.integration.camps.test_camps import _payload, _submit, _values

pytestmark = pytest.mark.django_db


def test_camps_initial_hold_needs_no_quote_or_money(camp_session: CampSession) -> None:
    """Подача заявки удерживает всю группу без финансовых обязательств."""
    app = _submit(camp_session, count=2)
    assert app.status == "new" and app.has_active_reservation
    assert app.reservation_expires_at == reservation_limit(camp_session)
    assert app.quoted_total is None and app.paid_amount == 0
    assert not CampPayment.objects.exists()
    assert camp_session.confirmed_players == 0
    assert camp_session.reserved_players == 2
    assert camp_session.available_places == 0
    waiting = _submit(camp_session)
    assert waiting.status == "waitlist" and waiting.reservation_expires_at is None


def test_camps_reserves_whole_group_and_ignores_companions(
    camp_session: CampSession,
) -> None:
    """Сопровождающий не занимает корт, частичная бронь группы недопустима."""
    first = _submit(camp_session)
    waiting = _submit(camp_session, count=2)
    assert waiting.status == "waitlist" and camp_session.reserved_players == 1
    app = submit_application(
        session_id=camp_session.pk,
        submission_key=uuid.uuid4(),
        contact={
            "package": camp_session.packages.first(),
            "contact_name": "Контакт",
            "email": "guest@example.test",
            "phone": "+79990000000",
        },
        participants=[
            {"name": "Игрок", "role": "adult", "age": 30, "level": "amateur"},
            {"name": "Сопровождающий", "role": "companion", "age": 30, "level": ""},
        ],
        user=None,
    )
    assert app.has_active_reservation and first.has_active_reservation
    assert camp_session.reserved_players == 2


def test_camps_disabled_auto_hold_keeps_manual_workflow(
    camp_session: CampSession,
) -> None:
    """Отключение автоматической брони не выдаёт резерв новым заявкам."""
    camp_session.auto_reserve = False
    camp_session.save()
    app = _submit(camp_session, count=2)
    assert app.status == "new" and app.reservation_expires_at is None
    assert camp_session.available_places == 2


def test_camps_repeat_submission_does_not_extend(camp_session: CampSession) -> None:
    """Повтор той же формы сохраняет исходный срок."""
    key = uuid.uuid4()
    first = _submit(camp_session, key=key)
    repeated = _submit(camp_session, key=key)
    assert first.pk == repeated.pk
    assert first.reservation_expires_at == repeated.reservation_expires_at


def test_camps_confirm_own_hold_without_payment(
    camp_session: CampSession, manager: User
) -> None:
    """Подтверждение использует собственные места и не требует поступления."""
    app = _submit(camp_session, count=2)
    confirmed = update_application(
        application_id=app.pk, actor=manager, values=_values()
    )
    assert confirmed.status == "confirmed" and confirmed.reservation_expires_at is None
    assert camp_session.reserved_players == 0 and camp_session.confirmed_players == 2
    assert not CampPayment.objects.exists()


def test_camps_cannot_confirm_seats_reserved_by_others(
    camp_session: CampSession, manager: User
) -> None:
    """Подтверждение листа ожидания не вытесняет действующую бронь."""
    _submit(camp_session, count=2)
    waiting = _submit(camp_session)
    with pytest.raises(ValidationError, match="недостаточно"):
        update_application(application_id=waiting.pk, actor=manager, values=_values())


def test_camps_expiry_releases_before_scheduler(
    camp_session: CampSession, manager: User
) -> None:
    """Проверка вместимости сразу исключает просроченные места."""
    app = _submit(camp_session, count=2)
    app.reservation_expires_at = timezone.now() - timedelta(seconds=1)
    app.save(update_fields=["reservation_expires_at"])
    assert camp_session.available_places == 2 and app.reservation_expired
    assert _submit(camp_session, count=2).has_active_reservation
    with pytest.raises(ValidationError):
        update_application(application_id=app.pk, actor=manager, values=_values())


def test_camps_expiry_is_idempotent_and_has_no_financial_effect(
    camp_session: CampSession, django_capture_on_commit_callbacks: Any
) -> None:
    """Фоновая обработка оставляет заявку и единожды уведомляет обе стороны."""
    app = _submit(camp_session)
    app.reservation_expires_at = timezone.now() - timedelta(seconds=1)
    app.save(update_fields=["reservation_expires_at"])
    with patch("apps.camps.reservations.send_camp_event") as send:
        assert expire_reservations(dry_run=True) == 1
        app.refresh_from_db()
        assert app.status == "new" and send.call_count == 0
        with django_capture_on_commit_callbacks(execute=True):
            assert expire_reservations() == 1
        assert send.call_count == 2
        with django_capture_on_commit_callbacks(execute=True):
            assert expire_reservations() == 0
        assert send.call_count == 2
    app.refresh_from_db()
    assert app.status == "waitlist" and app.reservation_expired
    assert camp_session.available_places == 2 and not CampPayment.objects.exists()


def test_camps_manual_extension_idempotence(
    camp_session: CampSession, manager: User, django_capture_on_commit_callbacks: Any
) -> None:
    """Повтор сохранения срока не дублирует уведомление, уменьшение запрещено."""
    app = _submit(camp_session)
    app.reservation_expires_at = timezone.now() + timedelta(hours=2)
    app.save(update_fields=["reservation_expires_at"])
    deadline = app.reservation_expires_at + timedelta(hours=4)
    with patch("apps.camps.reservations.send_camp_event") as send:
        with django_capture_on_commit_callbacks(execute=True):
            reserve_application(
                application_id=app.pk, actor=manager, expires_at=deadline
            )
            reserve_application(
                application_id=app.pk, actor=manager, expires_at=deadline
            )
        assert send.call_count == 1
    with pytest.raises(ValidationError, match="сократить"):
        reserve_application(
            application_id=app.pk,
            actor=manager,
            expires_at=deadline - timedelta(hours=1),
        )


def test_camps_waiting_group_reacquires_only_free_seats(
    camp_session: CampSession, manager: User
) -> None:
    """Координатор может выдать бронь листу ожидания после освобождения группы."""
    occupied = _submit(camp_session, count=2)
    waiting = _submit(camp_session, count=2)
    deadline = default_reservation_deadline(camp_session)
    with pytest.raises(ValidationError, match="Недостаточно"):
        reserve_application(
            application_id=waiting.pk, actor=manager, expires_at=deadline
        )
    update_application(
        application_id=occupied.pk, actor=manager, values=_values("cancelled")
    )
    held = reserve_application(
        application_id=waiting.pk, actor=manager, expires_at=deadline
    )
    assert held.status == "contacted" and held.has_active_reservation


@pytest.mark.parametrize("offset", [-1, 24 * 32])
def test_camps_rejects_past_or_after_start_day(
    camp_session: CampSession, manager: User, offset: int
) -> None:
    """Нельзя резервировать прошедшее время или дни после начала поездки."""
    app = _submit(camp_session)
    with pytest.raises(ValidationError):
        reserve_application(
            application_id=app.pk,
            actor=manager,
            expires_at=timezone.now() + timedelta(hours=offset),
        )


def test_camps_default_deadline_capped_by_start_day(camp_session: CampSession) -> None:
    """Даже длинный период заканчивается не позднее дня начала заезда."""
    assert default_reservation_deadline(camp_session) == reservation_limit(camp_session)


def test_camps_hold_requires_manager(camp_session: CampSession) -> None:
    """Участник не может продлевать себе места через бизнес-операцию."""
    app = _submit(camp_session)
    actor = User.objects.create_user(email="player@example.test", password="test")
    with pytest.raises(PermissionDenied):
        reserve_application(
            application_id=app.pk,
            actor=actor,
            expires_at=default_reservation_deadline(camp_session),
        )


@pytest.mark.parametrize("status", ["waitlist", "cancelled"])
def test_camps_status_release(
    camp_session: CampSession, manager: User, status: str
) -> None:
    """Прямой перевод в ожидание или отмену снимает текущую бронь."""
    app = _submit(camp_session)
    result = update_application(
        application_id=app.pk, actor=manager, values=_values(status)
    )
    assert result.reservation_expires_at is None and camp_session.available_places == 2


def test_camps_pending_cancellation_does_not_extend_hold(
    camp_session: CampSession, manager: User
) -> None:
    """Запрос отмены не даёт продлить резерв и не мешает его истечению."""
    app = _submit(camp_session)
    request = _request(app)
    with pytest.raises(ValidationError, match="отмену"):
        reserve_application(
            application_id=app.pk,
            actor=manager,
            expires_at=app.reservation_expires_at + timedelta(hours=1),
        )
    app.reservation_expires_at = timezone.now() - timedelta(seconds=1)
    app.save(update_fields=["reservation_expires_at"])
    assert expire_reservations() == 1
    request.refresh_from_db()
    assert request.status == "pending" and camp_session.available_places == 2
    decide_cancellation(
        request_id=request.pk,
        application_id=app.pk,
        actor=manager,
        decision="approved",
        resolution="Отмена согласована",
    )
    app.refresh_from_db()
    assert app.status == "cancelled" and app.reservation_expires_at is None


def test_camps_capacity_cannot_shrink_under_reservations(
    camp_session: CampSession,
) -> None:
    """Настройки вместимости учитывают временно удерживаемые места."""
    _submit(camp_session, count=2)
    camp_session.capacity = 1
    with pytest.raises(ValidationError):
        camp_session.full_clean()


def test_camps_hold_ui_and_coordinator_post(
    client: Client, camp_session: CampSession, manager: User
) -> None:
    """Участник видит срок, команда может сохранить новый срок с локальным временем."""
    response = client.post(
        reverse("camps:apply", args=[camp_session.pk]), _payload(client, camp_session)
    )
    body = client.get(response.url).content.decode()
    assert (
        "Места временно забронированы" in body
        and "Временная бронь не требует оплаты" in body
    )
    from apps.camps.models import CampApplication

    app = CampApplication.objects.get()
    client.force_login(manager)
    url = reverse("camps:manage_application", args=[app.pk])
    page = client.get(url)
    assert page.status_code == 200 and "Europe/Moscow" in page.content.decode()
    deadline = timezone.localtime(app.reservation_expires_at).replace(
        second=0, microsecond=0
    )
    response = client.post(
        url, {"action": "reserve", "expires_at": deadline.strftime("%Y-%m-%dT%H:%M")}
    )
    assert response.status_code == 302
    app.refresh_from_db()
    assert app.reservation_expires_at == deadline


def test_camps_reservation_command_dry_run(camp_session: CampSession) -> None:
    """Проверочный запуск команды ничего не сохраняет."""
    app = _submit(camp_session)
    app.reservation_expires_at = timezone.now() - timedelta(seconds=1)
    app.save(update_fields=["reservation_expires_at"])
    call_command("process_camp_reservations", dry_run=True)
    app.refresh_from_db()
    assert app.status == "new"
    call_command("process_camp_reservations")
    app.refresh_from_db()
    assert app.status == "waitlist"
