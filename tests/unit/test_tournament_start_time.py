"""Назначенный старт, обязательное время короткого турнира и поиск корта."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from typing import Any

import pytest
from django.contrib import admin
from django.contrib.admin.widgets import AutocompleteSelect
from django.test import RequestFactory
from django.utils import timezone

from apps.clubs.forms import ClubTournamentCreateForm
from apps.tournaments.admin import (
    TournamentAdmin,
    TournamentAdminForm,
    TVDTournamentAdmin,
    TVDTournamentAdminForm,
)
from apps.tournaments.exports.regulation import build_regulation_context
from apps.tournaments.models import (
    Tournament,
    TournamentDuration,
    TournamentTeam,
    TournamentVariant,
    TVDTournament,
)
from apps.tournaments.seo import build_tournament_sports_event_json
from tests.support.factories import make_club, make_player, make_tournament, make_user
from tests.unit.test_tournament_duration import _form_data

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("kind", ["admin", "groups", "club"])
@pytest.mark.parametrize("duration", TournamentDuration.values)
def test_start_time_is_required_only_for_single_day(kind: str, duration: str) -> None:
    """Все формы требуют время одного дня независимо от структуры сетки."""
    data = _form_data(duration=duration, start_time="")
    form: Any
    if kind == "club":
        form = ClubTournamentCreateForm(data=data, club=make_club())
    else:
        form_type = TVDTournamentAdminForm if kind == "groups" else TournamentAdminForm
        form = form_type(data=data)
    assert form.is_valid() is (duration != TournamentDuration.SINGLE_DAY), form.errors
    if duration == TournamentDuration.SINGLE_DAY:
        assert "start_time" in form.errors


@pytest.mark.parametrize("kind", ["admin", "groups", "club"])
def test_single_day_time_survives_fill_mode(kind: str) -> None:
    """Даже без назначенной даты время старта сохраняется без подстановок."""
    data = _form_data(duration="single", start_time="10:30", start_after_fill="on")
    form: Any
    if kind == "club":
        form = ClubTournamentCreateForm(data=data, club=make_club())
    else:
        form_type = TVDTournamentAdminForm if kind == "groups" else TournamentAdminForm
        form = form_type(data=data)
    assert form.is_valid(), form.errors
    saved = form.save()
    saved.refresh_from_db()
    assert saved.start_time == time(10, 30)
    assert saved.start_date is None


def test_assigned_start_is_visible_before_bracket() -> None:
    """Дата и время объявленного события не скрываются во время набора."""
    tournament = make_tournament(duration="single", start_time=time(10, 30))
    assert tournament.start_date_is_pending is False


def test_legacy_annual_placeholder_stays_hidden() -> None:
    """Исторический технический диапазон плюс год не становится расписанием."""
    tournament = make_tournament()
    tournament.end_date = tournament.start_date + timedelta(days=365)
    assert tournament.start_date_is_pending is True


def test_deadline_cannot_be_after_single_day_start_time() -> None:
    """Регистрация не заканчивается позже назначенного старта в локальной TZ."""
    data = _form_data(duration="single", start_time="10:30")
    start = datetime.fromisoformat(data["start_date"])
    data["registration_deadline"] = timezone.make_aware(start.replace(hour=11))
    form = TournamentAdminForm(data=data)
    assert not form.is_valid()
    assert "registration_deadline" in form.errors


def test_start_time_is_exported_with_timezone() -> None:
    """JSON-LD и регламент используют объявленные часы, а не полночь."""
    tournament = make_tournament(duration="single", start_time=time(10, 30))
    data = json.loads(
        build_tournament_sports_event_json(
            tournament,
            absolute_url="https://test.local/tournament/",
            site_base_url="https://test.local",
        )
    )
    assert (
        data["startDate"]
        == timezone.make_aware(
            datetime.combine(tournament.start_date, time(10, 30))
        ).isoformat()
    )
    regulation = build_regulation_context(
        tournament, public_url="https://test.local/tournament/", platform_name="Tennis"
    )
    assert "10:30" in str(regulation)


@pytest.mark.parametrize("variant", TournamentVariant.values)
@pytest.mark.parametrize("capacity", [None, 2])
@pytest.mark.parametrize("registered_count", [0, 1, 2])
def test_start_time_json_ld_offer_matches_available_slots(
    variant: str, capacity: int | None, registered_count: int
) -> None:
    """Предложение JSON-LD закрывается только после заполнения всех мест или пар."""
    tournament = make_tournament(
        duration="single",
        start_time=time(10, 30),
        variant=variant,
        entry_fee=1000,
        max_participants=capacity,
        max_teams=capacity,
    )
    for index in range(registered_count):
        player = make_player(email_suffix=f"offer-player-{index}")
        tournament.participants.add(player)
        if variant == TournamentVariant.DOUBLES:
            partner = make_player(email_suffix=f"offer-partner-{index}")
            tournament.participants.add(partner)
            TournamentTeam.objects.create(
                tournament=tournament, player1=player, player2=partner
            )
    data = json.loads(
        build_tournament_sports_event_json(
            tournament,
            absolute_url="https://test.local/tournament/",
            site_base_url="https://test.local",
        )
    )
    availability = (
        "InStock" if capacity is None or registered_count < capacity else "SoldOut"
    )
    assert data["offers"]["availability"] == f"https://schema.org/{availability}"


@pytest.mark.parametrize("groups", [False, True])
def test_admin_court_is_searchable(groups: bool) -> None:
    """Оба раздела админки используют поиск площадки по названию."""
    request = RequestFactory().get("/admin/tournaments/tournament/add/")
    request.user = make_user(is_staff=True, is_superuser=True)
    model_admin = (
        TVDTournamentAdmin(TVDTournament, admin.site)
        if groups
        else TournamentAdmin(Tournament, admin.site)
    )
    widget = model_admin.get_form(request)().fields["court"].widget
    assert isinstance(widget.widget, AutocompleteSelect)
