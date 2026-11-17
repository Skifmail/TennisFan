"""Продление и возобновление сохраняют календарь события и финансовую историю."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.tournaments.cancel import (
    TournamentLifecycleError,
    extend_registration,
    reopen_tournament,
)
from apps.tournaments.forms import TournamentRegistrationWindowForm
from apps.tournaments.models import (
    Tournament,
    TournamentDuration,
    TournamentEntryPayment,
    TournamentPostpaymentInvoice,
    TournamentStatus,
)
from tests.support.factories import make_player, make_tournament, make_user

pytestmark = pytest.mark.django_db


def _local_datetime(day: date, hour: int, minute: int = 0) -> datetime:
    """Собрать дату и время в текущем часовом поясе сайта."""
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _change_window(
    tournament: Tournament,
    action: str,
    *,
    start_date: date,
    registration_deadline: datetime,
    **kwargs: Any,
) -> Tournament:
    """Проверить один контракт календаря через оба действия жизненного цикла."""
    if action == "reopen":
        return reopen_tournament(
            tournament,
            start_date=start_date,
            registration_deadline=registration_deadline,
            notify=False,
            **kwargs,
        )
    return extend_registration(
        tournament,
        start_date=start_date,
        registration_deadline=registration_deadline,
        **kwargs,
    )


def _lifecycle_cup(action: str, **kwargs: Any) -> Tournament:
    """Создать турнир до сетки с календарём в будущем."""
    return make_tournament(
        start_date=timezone.localdate() + timedelta(days=10),
        status=(
            TournamentStatus.CANCELLED
            if action == "reopen"
            else TournamentStatus.UPCOMING
        ),
        start_time=time(11, 30),
        entry_fee=1000,
        **kwargs,
    )


@pytest.mark.parametrize("action", ["extend", "reopen"])
@pytest.mark.parametrize("shift_days", [-2, 20])
@pytest.mark.parametrize(
    ("duration", "span_days"),
    [
        (TournamentDuration.SINGLE_DAY, 0),
        (TournamentDuration.WEEKEND, 0),
        (TournamentDuration.WEEKEND, 1),
        (TournamentDuration.MULTI_DAY, 12),
        (TournamentDuration.MULTI_DAY, None),
    ],
)
def test_lifecycle_start_shift_preserves_event_length_and_existing_time(
    action: str, shift_days: int, duration: str, span_days: int | None
) -> None:
    """Намеренный перенос сдвигает обе даты, а не сокращает событие до нового старта."""
    start = timezone.localdate() + timedelta(days=10)
    end = start + timedelta(days=span_days) if span_days is not None else None
    tournament = _lifecycle_cup(action, duration=duration, end_date=end)
    new_start = start + timedelta(days=shift_days)
    deadline = _local_datetime(new_start - timedelta(days=1), 18)

    result = _change_window(
        tournament, action, start_date=new_start, registration_deadline=deadline
    )

    tournament.refresh_from_db()
    expected_end = (
        new_start + timedelta(days=span_days) if span_days is not None else None
    )
    assert tournament.start_date == new_start
    assert tournament.end_date == expected_end
    assert tournament.duration == duration
    assert tournament.start_time == time(11, 30)
    assert tournament.registration_deadline == deadline
    assert tournament.status == TournamentStatus.UPCOMING
    assert result.pk == tournament.pk
    assert result.start_date == new_start


@pytest.mark.parametrize("action", ["extend", "reopen"])
@pytest.mark.parametrize("zone", ["Europe/Moscow", "America/Los_Angeles"])
def test_lifecycle_can_reschedule_time_and_accept_deadline_at_exact_start(
    action: str, zone: str
) -> None:
    """Сравнение дедлайна со стартом учитывает новое время и локальный часовой пояс."""
    with timezone.override(zone):
        tournament = _lifecycle_cup(action, duration=TournamentDuration.SINGLE_DAY)
        start = tournament.start_date
        assert start is not None
        deadline = _local_datetime(start, 17, 30)
        _change_window(
            tournament,
            action,
            start_date=start,
            start_time=time(17, 30),
            registration_deadline=deadline,
        )
        tournament.refresh_from_db()
        assert tournament.start_time == time(17, 30)
        assert tournament.get_start_datetime() == deadline
        assert not tournament.get_calendar_errors()


@pytest.mark.parametrize("action", ["extend", "reopen"])
def test_lifecycle_rejects_deadline_after_saved_start_time_atomically(
    action: str,
) -> None:
    """Дедлайн в ту же дату, но после старта отклоняется без частичного переноса."""
    tournament = _lifecycle_cup(action, duration=TournamentDuration.WEEKEND)
    before = Tournament.objects.filter(pk=tournament.pk).values().get()
    new_start = tournament.start_date + timedelta(days=3)
    with pytest.raises(TournamentLifecycleError, match="времени начала"):
        _change_window(
            tournament,
            action,
            start_date=new_start,
            registration_deadline=_local_datetime(new_start, 12),
        )
    assert Tournament.objects.filter(pk=tournament.pk).values().get() == before
    assert tournament.start_date == before["start_date"]
    assert tournament.end_date == before["end_date"]


@pytest.mark.parametrize("action", ["extend", "reopen"])
def test_lifecycle_requires_time_for_legacy_single_day(action: str) -> None:
    """Старый однодневный турнир без времени нельзя перенести без назначения времени."""
    tournament = _lifecycle_cup(action, duration=TournamentDuration.SINGLE_DAY)
    Tournament.objects.filter(pk=tournament.pk).update(start_time=None)
    tournament.refresh_from_db()
    start = tournament.start_date
    assert start is not None
    before = Tournament.objects.filter(pk=tournament.pk).values().get()
    with pytest.raises(TournamentLifecycleError, match="время"):
        _change_window(
            tournament,
            action,
            start_date=start,
            registration_deadline=_local_datetime(start - timedelta(days=1), 18),
        )
    assert Tournament.objects.filter(pk=tournament.pk).values().get() == before


@pytest.mark.parametrize("action", ["extend", "reopen"])
def test_lifecycle_preserves_open_postpayment_window_and_invoice_snapshots(
    action: str,
) -> None:
    """Перенос дат не запускает окно заново и не переписывает суммы, сроки и статусы."""
    started_at = timezone.now() - timedelta(hours=1)
    tournament = _lifecycle_cup(
        action,
        duration=TournamentDuration.MULTI_DAY,
        allow_postpayment=True,
        postpayment_window_started_at=started_at,
        postpayment_deadline_hours=12,
    )
    for index, status in enumerate(("pending", "paid", "cancelled", "expired")):
        player = make_player(email_suffix=f"calendar-history-{index}")
        tournament.participants.add(player)
        TournamentPostpaymentInvoice.objects.create(
            tournament=tournament,
            user=player.user,
            amount=Decimal("700.00") + index,
            status=status,
            due_at=started_at + timedelta(hours=8 + index),
            paid_at=started_at if status == "paid" else None,
            reminder_1h_sent_at=started_at,
            payment_link_resent_at=started_at,
        )
        if status == "paid":
            TournamentEntryPayment.objects.create(
                tournament=tournament, user=player.user
            )
    invoice_history = list(tournament.postpayment_invoices.order_by("pk").values())
    entry_history = list(tournament.entry_payments.order_by("pk").values())
    new_start = tournament.start_date + timedelta(days=20)
    _change_window(
        tournament,
        action,
        start_date=new_start,
        registration_deadline=_local_datetime(new_start - timedelta(days=1), 18),
    )
    tournament.refresh_from_db()
    assert tournament.postpayment_window_started_at == started_at
    assert tournament.postpayment_deadline_hours == 12
    assert tournament.allow_postpayment is True
    assert (
        list(tournament.postpayment_invoices.order_by("pk").values()) == invoice_history
    )
    assert list(tournament.entry_payments.order_by("pk").values()) == entry_history
    assert tournament.participants.count() == 4


def _manage_action_url(tournament: Tournament, action: str) -> str:
    """Получить URL продления или возобновления без ручной сборки пути."""
    return reverse(
        (
            "tournament_manage_reopen"
            if action == "reopen"
            else "tournament_manage_extend_registration"
        ),
        kwargs={"slug": tournament.slug},
    )


@pytest.mark.parametrize("action", ["extend", "reopen"])
def test_lifecycle_post_saves_new_start_time(client: Client, action: str) -> None:
    """Панель управления передаёт выбранное время в сервис переноса календаря."""
    manager = make_user(email="calendar-manager@test.local", is_staff=True)
    client.force_login(manager)
    tournament = _lifecycle_cup(action, duration=TournamentDuration.SINGLE_DAY)
    start = tournament.start_date + timedelta(days=3)
    response = client.post(
        _manage_action_url(tournament, action),
        {
            "start_date": start.isoformat(),
            "start_time": "17:30",
            "registration_deadline": f"{start.isoformat()}T17:00",
        },
        secure=True,
    )
    assert response.status_code == 302
    tournament.refresh_from_db()
    assert tournament.start_date == start
    assert tournament.end_date == start
    assert tournament.start_time == time(17, 30)


@pytest.mark.parametrize("action", ["extend", "reopen"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("start_date", "2026-13-40"),
        ("registration_deadline", "2026-13-40T25:99"),
        ("start_time", "25:10"),
        ("start_time", ""),
    ],
)
def test_lifecycle_bad_post_retains_values_and_reports_field_error(
    client: Client, action: str, field: str, value: str
) -> None:
    """Неверный ввод не даёт 500, не меняет турнир и остаётся в форме рядом с ошибкой."""
    manager = make_user(email="calendar-validation@test.local", is_staff=True)
    client.force_login(manager)
    tournament = _lifecycle_cup(action, duration=TournamentDuration.SINGLE_DAY)
    start = tournament.start_date + timedelta(days=3)
    data = {
        "start_date": start.isoformat(),
        "start_time": "17:30",
        "registration_deadline": f"{start.isoformat()}T17:00",
        field: value,
    }
    before = Tournament.objects.filter(pk=tournament.pk).values().get()
    response = client.post(_manage_action_url(tournament, action), data, secure=True)
    assert response.status_code == 200
    form = response.context["lifecycle_form"]
    assert field in form.errors
    assert form[field].value() == value
    assert Tournament.objects.filter(pk=tournament.pk).values().get() == before
    assert 'id="lifecycle-errors"' in response.content.decode()


@pytest.mark.parametrize("action", ["extend", "reopen"])
@pytest.mark.parametrize("deadline_kind", ["past", "after_date", "after_time"])
def test_lifecycle_calendar_post_links_error_to_registration_deadline(
    client: Client, action: str, deadline_kind: str
) -> None:
    """Календарный отказ сохраняет дедлайн, inline-ошибку и ссылку на поле в сводке."""
    manager = make_user(email="calendar-error-link@test.local", is_staff=True)
    client.force_login(manager)
    tournament = _lifecycle_cup(action, duration=TournamentDuration.SINGLE_DAY)
    assert tournament.start_date is not None
    new_start = tournament.start_date + timedelta(days=3)
    deadline_day = {
        "past": timezone.localdate() - timedelta(days=1),
        "after_date": new_start + timedelta(days=1),
        "after_time": new_start,
    }[deadline_kind]
    data = {
        "start_date": new_start.isoformat(),
        "start_time": "17:30",
        "registration_deadline": f"{deadline_day.isoformat()}T18:00",
    }
    before = Tournament.objects.filter(pk=tournament.pk).values().get()

    response = client.post(_manage_action_url(tournament, action), data, secure=True)

    assert response.status_code == 200
    form = response.context["lifecycle_form"]
    assert isinstance(form, TournamentRegistrationWindowForm)
    assert "registration_deadline" in form.errors
    assert not form.non_field_errors()
    assert form["registration_deadline"].value() == data["registration_deadline"]
    assert form["start_date"].value() == data["start_date"]
    assert form["start_time"].value() == data["start_time"]
    field_id = form["registration_deadline"].id_for_label
    assert f'href="#{field_id}"' in response.content.decode()
    assert Tournament.objects.filter(pk=tournament.pk).values().get() == before


@pytest.mark.parametrize("action", ["extend", "reopen"])
def test_lifecycle_status_post_keeps_non_field_error(
    client: Client, action: str
) -> None:
    """Отказ по состоянию турнира не приписывается корректному календарному полю."""
    manager = make_user(email="calendar-state-error@test.local", is_staff=True)
    client.force_login(manager)
    tournament = _lifecycle_cup(action, duration=TournamentDuration.SINGLE_DAY)
    Tournament.objects.filter(pk=tournament.pk).update(
        status=TournamentStatus.COMPLETED
    )
    assert tournament.start_date is not None
    start = tournament.start_date + timedelta(days=3)
    before = Tournament.objects.filter(pk=tournament.pk).values().get()

    response = client.post(
        _manage_action_url(tournament, action),
        {
            "start_date": start.isoformat(),
            "start_time": "17:30",
            "registration_deadline": f"{start.isoformat()}T17:00",
        },
        secure=True,
    )

    assert response.status_code == 200
    form = response.context["lifecycle_form"]
    assert isinstance(form, TournamentRegistrationWindowForm)
    assert form.non_field_errors()
    assert "registration_deadline" not in form.errors
    assert Tournament.objects.filter(pk=tournament.pk).values().get() == before
