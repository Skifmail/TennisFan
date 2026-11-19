"""Продолжительность турнира не зависит от структуры сетки."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.clubs.forms import ClubTournamentCreateForm
from apps.tournaments.admin import (
    TournamentAdmin,
    TournamentAdminForm,
    TVDTournamentAdmin,
    TVDTournamentAdminForm,
    backfill_tvd_standings_action,
)
from apps.tournaments.models import (
    SeasonPoints,
    Tournament,
    TournamentDuration,
    TournamentFormat,
    TournamentPlayerResult,
    TournamentStatus,
    TVDTournament,
)
from tests.support.factories import make_club, make_player, make_tournament, make_user

pytestmark = pytest.mark.django_db


def _form_data(**overrides: Any) -> dict[str, Any]:
    """Данные формы с явным выбором продолжительности и без старой галочки."""
    data: dict[str, Any] = {
        "name": "Кубок",
        "slug": "duration-form-cup",
        "city": "Санкт-Петербург",
        "sport": "tennis",
        "format": TournamentFormat.SINGLE_ELIMINATION,
        "variant": "singles",
        "gender": "male",
        "duration": TournamentDuration.MULTI_DAY,
        "tournament_type": "regular",
        "status": "upcoming",
        "entry_fee": "1000",
        "allowed_categories": ["amateur"],
        "start_date": (timezone.localdate() + timedelta(days=2)).isoformat(),
        "start_time": "10:30",
        "max_participants": "16",
        "match_days_per_round": "7",
        "postpayment_deadline_hours": "12",
        "fan_points_r1": "10",
        "fan_points_r2": "25",
        "fan_points_sf": "45",
        "fan_points_final": "70",
        "fan_points_winner": "100",
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize("tournament_format", TournamentFormat.values)
@pytest.mark.parametrize("duration", TournamentDuration.values)
def test_duration_is_preserved_for_every_format(
    tournament_format: str, duration: str
) -> None:
    """Любая структура допускает каждый вариант продолжительности."""
    tournament = make_tournament(format=tournament_format, duration=duration)
    tournament.refresh_from_db()

    assert tournament.duration == duration
    assert tournament.is_one_day is (duration == TournamentDuration.SINGLE_DAY)
    assert tournament.format == tournament_format


@pytest.mark.parametrize(
    "tournament_format",
    [TournamentFormat.SINGLE_ELIMINATION, TournamentFormat.WEEKEND_DAY],
)
@pytest.mark.parametrize("duration", TournamentDuration.values)
def test_public_tournament_instructions_follow_duration(
    client: Client, tournament_format: str, duration: str
) -> None:
    """Обе версии карточки показывают порядок проведения по продолжительности."""
    tournament = make_tournament(
        format=tournament_format,
        duration=duration,
        start_date=timezone.localdate() + timedelta(days=7),
        status=TournamentStatus.UPCOMING,
        match_days_per_round=7,
    )

    response = client.get(
        reverse("tournament_detail", args=[tournament.slug]), secure=True
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert content.count("Как проходит турнир") == 1
    is_single_day = duration == TournamentDuration.SINGLE_DAY
    assert ("Все матчи пройдут в один день" in content) is is_single_day
    assert ("Договоритесь с соперником" in content) is not is_single_day
    assert ("На каждый раунд отводится 7 дней" in content) is not is_single_day
    round_days_label = (
        "Дней на раунд"
        if tournament_format == TournamentFormat.WEEKEND_DAY
        else "7 дн. на раунд"
    )
    assert (round_days_label in content) is not is_single_day


@pytest.mark.parametrize("tournament_format", TournamentFormat.values)
def test_legacy_one_day_creation_still_sets_single_duration(
    tournament_format: str,
) -> None:
    """Старый вызов с is_one_day=True сохраняет однодневность и оплату."""
    tournament = make_tournament(format=tournament_format, is_one_day=True)
    tournament.refresh_from_db()

    assert tournament.duration == TournamentDuration.SINGLE_DAY
    assert tournament.is_one_day is True


def test_explicit_duration_takes_priority_over_legacy_creation_flag() -> None:
    """Явная продолжительность главнее противоречащей устаревшей галочки."""
    tournament = make_tournament(
        duration=TournamentDuration.MULTI_DAY,
        is_one_day=True,
        entry_fee=1000,
        allow_postpayment=True,
    )
    tournament.refresh_from_db()

    assert tournament.duration == TournamentDuration.MULTI_DAY
    assert tournament.is_one_day is False
    assert tournament.allow_postpayment is True


def test_partial_duration_save_preserves_postpayment_choice() -> None:
    """Частичный переход к одному дню синхронизирует флаг, не меняя способ оплаты."""
    tournament = make_tournament(entry_fee=1000, allow_postpayment=True)
    tournament.duration = TournamentDuration.SINGLE_DAY
    tournament.save(update_fields=["duration"])
    tournament.refresh_from_db()

    assert tournament.duration == TournamentDuration.SINGLE_DAY
    assert tournament.is_one_day is True
    assert tournament.allow_postpayment is True


def test_partial_duration_save_can_switch_back_to_multi_day() -> None:
    """Старая галочка не мешает явно переключить однодневный турнир обратно."""
    tournament = make_tournament(is_one_day=True)
    tournament.duration = TournamentDuration.MULTI_DAY
    tournament.save(update_fields=["duration"])
    tournament.refresh_from_db()

    assert tournament.duration == TournamentDuration.MULTI_DAY
    assert tournament.is_one_day is False


@pytest.mark.parametrize("partial", [False, True])
def test_legacy_flag_update_keeps_duration_consistent(partial: bool) -> None:
    """Старые интеграции, меняющие только флаг, продолжают работать."""
    tournament = make_tournament()
    tournament.is_one_day = True
    tournament.save(update_fields=["is_one_day"] if partial else None)
    tournament.refresh_from_db()
    assert tournament.duration == TournamentDuration.SINGLE_DAY

    tournament.is_one_day = False
    tournament.save(update_fields=["is_one_day"] if partial else None)
    tournament.refresh_from_db()
    assert tournament.duration == TournamentDuration.MULTI_DAY


def test_unrelated_partial_save_does_not_persist_unsaved_duration() -> None:
    """Сохранение названия не протаскивает несохранённые настройки оплаты."""
    tournament = make_tournament(entry_fee=1000, allow_postpayment=True)
    tournament.duration = TournamentDuration.SINGLE_DAY
    tournament.name = "Новое название"
    tournament.save(update_fields=["name"])
    tournament.refresh_from_db()

    assert tournament.duration == TournamentDuration.MULTI_DAY
    assert tournament.is_one_day is False
    assert tournament.allow_postpayment is True


def test_partial_start_save_uses_saved_duration_for_missing_end() -> None:
    """Несохранённая продолжительность не создаёт фиктивную дату окончания."""
    tournament = make_tournament(duration=TournamentDuration.MULTI_DAY)
    tournament.duration = TournamentDuration.SINGLE_DAY
    tournament.start_date = timezone.localdate() + timedelta(days=3)
    tournament.save(update_fields=["start_date"])
    tournament.refresh_from_db()

    assert tournament.duration == TournamentDuration.MULTI_DAY
    assert tournament.end_date is None


def test_partial_start_save_preserves_unsaved_explicit_end() -> None:
    """Частичное сохранение старта не затирает явно сохранённый конец события."""
    start = timezone.localdate() + timedelta(days=2)
    end = start + timedelta(days=1)
    tournament = make_tournament(
        duration=TournamentDuration.WEEKEND, start_date=start, end_date=end
    )
    tournament.end_date = None
    tournament.start_date = start + timedelta(days=1)
    tournament.save(update_fields=["start_date"])
    tournament.refresh_from_db()

    assert tournament.end_date == end


def test_groups_allow_postpayment_when_duration_is_multi_day() -> None:
    """Групповой формат не запрещает постоплату сам по себе."""
    tournament = make_tournament(
        format=TournamentFormat.WEEKEND_DAY,
        duration=TournamentDuration.MULTI_DAY,
        entry_fee=1000,
        allow_postpayment=True,
    )
    tournament.refresh_from_db()
    assert tournament.allow_postpayment is True


def test_main_admin_has_one_duration_setting_and_group_actions() -> None:
    """Общий раздел показывает продолжительность и умеет управлять группами."""
    model_admin = TournamentAdmin(Tournament, admin.site)
    request = RequestFactory().get("/admin/tournaments/tournament/add/")
    request.user = make_user(is_staff=True, is_superuser=True)
    form = model_admin.get_form(request)()

    assert Tournament._meta.verbose_name_plural == "Турниры"
    assert "duration" in form.fields
    assert "is_one_day" not in form.fields
    assert "duration" in model_admin.list_display
    assert "duration" in model_admin.list_filter
    assert "generate_tvd_groups_action" in model_admin.get_actions(request)
    assert "generate_tvd_playoffs_action" in model_admin.get_actions(request)


@pytest.mark.parametrize(
    "tournament_format",
    [
        value
        for value in TournamentFormat.values
        if value != TournamentFormat.WEEKEND_DAY
    ],
)
def test_group_standings_backfill_preserves_other_formats(
    tournament_format: str,
) -> None:
    """Смешанный выбор не меняет результаты и сезонные очки остальных форматов."""
    other = make_tournament(format=tournament_format, status=TournamentStatus.COMPLETED)
    other_player = make_player(email_suffix="duration-backfill-other")
    other.participants.add(other_player)
    TournamentPlayerResult.objects.create(
        tournament=other,
        player=other_player,
        round_eliminated=TournamentPlayerResult.RoundEliminated.R1,
        fan_points=10,
    )
    results_before = list(
        TournamentPlayerResult.objects.filter(tournament=other).values()
    )
    points_before = list(SeasonPoints.objects.filter(player=other_player).values())
    grouped = make_tournament(
        slug="duration-backfill-grouped",
        format=TournamentFormat.WEEKEND_DAY,
        duration=TournamentDuration.WEEKEND,
        status=TournamentStatus.COMPLETED,
    )
    group_player = make_player(email_suffix="duration-backfill-grouped")
    grouped.participants.add(group_player)
    request = RequestFactory().post("/admin/tournaments/tournament/")
    request.user = make_user(is_staff=True, is_superuser=True)
    model_admin = TournamentAdmin(Tournament, admin.site)

    with patch("apps.tournaments.admin.messages.success"):
        backfill_tvd_standings_action(
            model_admin,
            request,
            Tournament.objects.filter(pk__in=(other.pk, grouped.pk)),
        )

    assert (
        list(TournamentPlayerResult.objects.filter(tournament=other).values())
        == results_before
    )
    assert (
        list(SeasonPoints.objects.filter(player=other_player).values()) == points_before
    )
    assert (
        TournamentPlayerResult.objects.get(
            tournament=grouped, player=group_player
        ).place
        == 1
    )


@pytest.mark.parametrize("in_club", [False, True])
def test_manual_format_saved_at_creation(in_club: bool) -> None:
    """Ручной формат сохраняется до запуска в обеих формах создания."""
    data = _form_data(format="manual")
    form = (
        ClubTournamentCreateForm(data=data, club=make_club())
        if in_club
        else TournamentAdminForm(data=data)
    )
    assert form.is_valid(), form.errors
    tournament = form.save()
    tournament.refresh_from_db()
    assert tournament.uses_manual_pairing()
    assert not tournament.bracket_generated
    assert not tournament.matches.exists()


def test_single_day_admin_form_is_valid_without_legacy_checkbox() -> None:
    """Однодневный бесплатный турнир выбирается одним выпадающим списком."""
    form = TournamentAdminForm(
        data=_form_data(duration=TournamentDuration.SINGLE_DAY, entry_fee="0")
    )
    assert form.is_valid(), form.errors
    tournament = form.save()
    tournament.refresh_from_db()
    assert tournament.is_one_day is True
    assert tournament.end_date == tournament.start_date


def test_multi_day_groups_admin_form_allows_postpayment() -> None:
    """В общей форме группы и постоплата совместимы при многодневности."""
    form = TournamentAdminForm(
        data=_form_data(format=TournamentFormat.WEEKEND_DAY, allow_postpayment="on")
    )
    assert form.is_valid(), form.errors
    assert form.save().allow_postpayment is True


def test_group_shortcut_admin_preserves_selected_duration() -> None:
    """Специализированный раздел фиксирует структуру, но не продолжительность."""
    model_admin = TVDTournamentAdmin(TVDTournament, admin.site)
    request = RequestFactory().get("/admin/tournaments/tvdtournament/add/")
    request.user = make_user(is_staff=True, is_superuser=True)
    form = model_admin.get_form(request)(
        data=_form_data(duration=TournamentDuration.WEEKEND, allow_postpayment="on")
    )
    assert "duration" in form.fields
    assert "is_one_day" not in form.fields
    assert "format" not in form.fields
    assert form.is_valid(), form.errors

    tournament = form.save(commit=False)
    model_admin.save_model(request, tournament, form, change=False)
    tournament.refresh_from_db()
    assert tournament.format == TournamentFormat.WEEKEND_DAY
    assert tournament.duration == TournamentDuration.WEEKEND
    assert tournament.is_one_day is False
    assert tournament.allow_postpayment is True


def test_free_group_shortcut_uses_the_same_one_day_rules() -> None:
    """Галочка бесплатности в старом разделе не требует фиктивного взноса."""
    form = TVDTournamentAdminForm(
        data=_form_data(
            duration=TournamentDuration.SINGLE_DAY, is_free="on", entry_fee="0"
        )
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["entry_fee"] == 0


@pytest.mark.parametrize(
    "group_admin", [False, True], ids=["main-admin", "groups-admin"]
)
def test_court_fee_checkbox_controls_public_notice(
    client: Client, group_admin: bool
) -> None:
    """Галочка из обеих админок управляет предупреждением рядом со взносом."""
    model_admin = (
        TVDTournamentAdmin(TVDTournament, admin.site)
        if group_admin
        else TournamentAdmin(Tournament, admin.site)
    )
    request = RequestFactory().get("/admin/")
    request.user = make_user(is_staff=True, is_superuser=True)
    data = _form_data(court_has_extra_fee="on")
    form = model_admin.get_form(request)(data=data)
    assert form.is_valid(), form.errors
    tournament = form.save(commit=False)
    model_admin.save_model(request, tournament, form, change=False)
    tournament.refresh_from_db()
    assert tournament.court_has_extra_fee is True

    url = reverse("tournament_detail", args=[tournament.slug])
    notice = "Корт на матчи оплачивается отдельно"
    response = client.get(url, secure=True)
    assert response.status_code == 200
    assert notice in response.content.decode()

    del data["court_has_extra_fee"]
    form = model_admin.get_form(request, tournament)(data=data, instance=tournament)
    assert form.is_valid(), form.errors
    tournament = form.save(commit=False)
    model_admin.save_model(request, tournament, form, change=True)
    tournament.refresh_from_db()
    assert tournament.court_has_extra_fee is False
    assert tournament.entry_fee == 1000
    response = client.get(url, secure=True)
    assert response.status_code == 200
    assert notice not in response.content.decode()


@pytest.mark.parametrize(
    ("duration", "end_offset", "error_field"),
    [
        (TournamentDuration.MULTI_DAY, -1, "end_date"),
        (TournamentDuration.SINGLE_DAY, 1, "end_date"),
        (TournamentDuration.WEEKEND, 2, "end_date"),
    ],
)
def test_model_rejects_dates_conflicting_with_duration(
    duration: str, end_offset: int, error_field: str
) -> None:
    """Ошибки календаря проверяются независимо от админки и города."""
    start = timezone.localdate() + timedelta(days=2)
    tournament = Tournament(
        city="Санкт-Петербург",
        start_date=start,
        end_date=start + timedelta(days=end_offset),
        duration=duration,
    )
    with pytest.raises(ValidationError) as exc_info:
        tournament.clean()
    assert error_field in exc_info.value.message_dict


def test_admin_rejects_registration_deadline_after_start() -> None:
    """Форма админки не разрешает регистрацию после даты начала."""
    data = _form_data()
    start = date.fromisoformat(data["start_date"])
    deadline = timezone.make_aware(
        datetime.combine(start + timedelta(days=1), datetime.min.time())
    )
    form = TournamentAdminForm(data={**data, "registration_deadline": deadline})
    assert not form.is_valid()
    assert "registration_deadline" in form.errors


@pytest.mark.parametrize("club_form", [False, True], ids=["admin", "club"])
@pytest.mark.parametrize("invalid_field", ["end_date", "registration_deadline"])
def test_unchanged_legacy_calendar_does_not_block_other_form_edits(
    club_form: bool, invalid_field: str
) -> None:
    """Старые ошибочные даты сохраняются с предупреждением при правке названия."""
    data = _form_data()
    start = date.fromisoformat(data["start_date"])
    tournament = make_tournament(
        slug=data["slug"],
        city=data["city"],
        start_date=start,
        entry_fee=1000,
    )
    invalid_value = (
        start - timedelta(days=1)
        if invalid_field == "end_date"
        else timezone.make_aware(
            datetime.combine(start + timedelta(days=1), datetime.min.time())
        )
    )
    Tournament.objects.filter(pk=tournament.pk).update(**{invalid_field: invalid_value})
    tournament.refresh_from_db()
    data.update(
        name="Исправленное название",
        start_time=tournament.start_time or "",
        **{invalid_field: invalid_value},
    )
    form = (
        ClubTournamentCreateForm(data=data, instance=tournament, club=make_club())
        if club_form
        else TournamentAdminForm(data=data, instance=tournament)
    )
    assert form.is_valid(), form.errors
    assert "Существующее значение" in str(form.fields[invalid_field].help_text)
    saved = form.save()
    saved.refresh_from_db()
    assert saved.name == "Исправленное название"
    assert getattr(saved, invalid_field) == invalid_value


@pytest.mark.parametrize("club_form", [False, True], ids=["admin", "club"])
def test_changing_legacy_calendar_still_requires_valid_dates(club_form: bool) -> None:
    """Исключение для истории не разрешает сохранять новые ошибочные даты."""
    data = _form_data()
    start = date.fromisoformat(data["start_date"])
    tournament = make_tournament(
        slug=data["slug"], city=data["city"], start_date=start, entry_fee=1000
    )
    Tournament.objects.filter(pk=tournament.pk).update(
        end_date=start - timedelta(days=1)
    )
    tournament.refresh_from_db()
    data["end_date"] = start - timedelta(days=2)
    form = (
        ClubTournamentCreateForm(data=data, instance=tournament, club=make_club())
        if club_form
        else TournamentAdminForm(data=data, instance=tournament)
    )
    assert not form.is_valid()
    assert "end_date" in form.errors


@pytest.mark.parametrize("club_form", [False, True], ids=["admin", "club"])
def test_duration_change_does_not_shorten_explicit_calendar(club_form: bool) -> None:
    """Переход к одному дню требует явно исправить даты, а не переписывает их."""
    data = _form_data(duration=TournamentDuration.SINGLE_DAY)
    start = date.fromisoformat(data["start_date"])
    end = start + timedelta(days=7)
    tournament = make_tournament(
        slug=data["slug"],
        city=data["city"],
        start_date=start,
        end_date=end,
        entry_fee=1000,
    )
    data["end_date"] = end
    form = (
        ClubTournamentCreateForm(data=data, instance=tournament, club=make_club())
        if club_form
        else TournamentAdminForm(data=data, instance=tournament)
    )
    assert not form.is_valid()
    assert "end_date" in form.errors
    tournament.refresh_from_db()
    assert tournament.duration == TournamentDuration.MULTI_DAY
    assert tournament.end_date == end


@pytest.mark.parametrize("club_form", [False, True], ids=["admin", "club"])
@pytest.mark.parametrize("generated", [False, True], ids=["recruiting", "started"])
def test_fill_form_preserves_explicit_existing_dates(
    club_form: bool, generated: bool
) -> None:
    """Редактирование турнира после набора не обнуляет явно назначенный календарь."""
    data = _form_data(duration=TournamentDuration.WEEKEND, start_after_fill="on")
    start = date.fromisoformat(data["start_date"])
    end = start + timedelta(days=1)
    deadline = timezone.make_aware(
        datetime.combine(start - timedelta(days=1), datetime.min.time())
    )
    tournament = make_tournament(
        slug=data["slug"],
        city=data["city"],
        duration=TournamentDuration.WEEKEND,
        entry_fee=1000,
        start_after_fill=True,
        start_date=start,
        end_date=end,
        registration_deadline=deadline,
        bracket_generated=generated,
    )
    data.update(
        end_date=end, registration_deadline=deadline, bracket_generated=generated
    )
    form = (
        ClubTournamentCreateForm(data=data, instance=tournament, club=make_club())
        if club_form
        else TournamentAdminForm(data=data, instance=tournament)
    )
    assert form.is_valid(), form.errors
    saved = form.save()
    saved.refresh_from_db()
    assert (saved.start_date, saved.end_date, saved.registration_deadline) == (
        start,
        end,
        deadline,
    )


def test_club_form_exposes_duration_instead_of_one_day_flag() -> None:
    """Клубный организатор использует такой же выбор, как администратор."""
    form = ClubTournamentCreateForm(
        data=_form_data(duration=TournamentDuration.WEEKEND, allow_postpayment="on"),
        club=make_club(),
    )
    assert "duration" in form.fields
    assert "is_one_day" not in form.fields
    assert form.is_valid(), form.errors
    tournament = form.save()
    assert tournament.duration == TournamentDuration.WEEKEND
    assert tournament.allow_postpayment is True


def test_old_club_post_with_one_day_flag_is_still_accepted() -> None:
    """Уже открытая старая клубная форма не теряет однодневный выбор."""
    data = _form_data(entry_fee="0", is_one_day="on")
    data.pop("duration")
    form = ClubTournamentCreateForm(data=data, club=make_club())
    assert form.is_valid(), form.errors
    assert form.save().duration == TournamentDuration.SINGLE_DAY


def test_old_club_querydict_preserves_multiple_allowed_categories() -> None:
    """Старый HTTP POST сохраняет все чекбоксы и не изменяется самой формой."""
    data = _form_data(
        entry_fee="0", is_one_day="on", allowed_categories=["novice", "amateur"]
    )
    data.pop("duration")
    post = QueryDict(urlencode(data, doseq=True))
    form = ClubTournamentCreateForm(data=post, club=make_club())

    assert form.is_valid(), form.errors
    assert form.cleaned_data["allowed_categories"] == ["novice", "amateur"]
    assert "duration" not in post
    assert form.save().duration == TournamentDuration.SINGLE_DAY
