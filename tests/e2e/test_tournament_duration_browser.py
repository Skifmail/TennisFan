"""Реальные формы турниров сохраняют календарь и независимую продолжительность."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from mimetypes import guess_type
from os import environ
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Page, Route, expect, sync_playwright

from apps.clubs.models import Club, ClubMember, ClubMemberRole, ClubMemberStatus
from apps.courts.models import Court
from apps.payments.models import PaymentRecord
from apps.tournaments.models import (
    Tournament,
    TournamentAllowedCategory,
    TournamentDuration,
    TournamentFormat,
)
from apps.users.models import Player
from tests.support.factories import (
    make_club,
    make_player,
    make_subscription,
    make_tournament,
    make_user,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not (environ.get("CHAT_BROWSER_EXECUTABLE") or environ.get("CHAT_E2E_URL")),
        reason="Браузер запускается на отдельном этапе CI",
    ),
]


@pytest.fixture
def managed_club(client: Client) -> Club:
    """Создать организатора с доступом к клубной форме и общей админке."""
    user = make_user(
        email="calendar-manager@test.local", is_staff=True, is_superuser=True
    )
    club = make_club(city="Санкт-Петербург")
    ClubMember.objects.create(
        club=club,
        user=user,
        role=ClubMemberRole.ADMIN,
        status=ClubMemberStatus.ACTIVE,
    )
    client.force_login(user)
    return club


@contextmanager
def _browser_page(
    document: bytes,
    page_path: str,
    i18n_document: bytes,
    *,
    ajax_documents: Mapping[tuple[str, str], bytes] | None = None,
    post_path: str | None = None,
) -> Iterator[tuple[Page, list[str], list[tuple[bytes, str]]]]:
    """Отдать реальные HTML и статику без внешней сети и перехватить отправку формы."""
    submitted: list[tuple[bytes, str]] = []
    errors: list[str] = []

    def serve(route: Route) -> None:
        """Подменить только транспорт, не выполняя запросы к рабочему серверу."""
        url = urlsplit(route.request.url)
        if url.netloc != "testserver":
            route.abort()
        elif url.path.startswith("/static/"):
            found = finders.find(url.path.removeprefix("/static/"))
            if isinstance(found, str):
                route.fulfill(
                    body=Path(found).read_bytes(),
                    content_type=guess_type(found)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404)
        elif url.path == reverse("admin:jsi18n"):
            route.fulfill(body=i18n_document, content_type="text/javascript")
        elif ajax_documents is not None and any(
            path == url.path for path, _ in ajax_documents
        ):
            query = parse_qs(url.query)
            needle = (query.get("q") or query.get("term") or [""])[0]
            route.fulfill(
                body=ajax_documents.get(
                    (url.path, needle),
                    b'{"results": [], "pagination": {"more": false}}',
                ),
                content_type="application/json",
            )
        elif url.path in {page_path, post_path}:
            if route.request.method == "POST":
                body = route.request.post_data_buffer
                assert body is not None
                submitted.append((body, route.request.headers["content-type"]))
                route.fulfill(
                    body="<!doctype html><title>Форма отправлена</title>",
                    content_type="text/html; charset=utf-8",
                )
            else:
                route.fulfill(body=document, content_type="text/html; charset=utf-8")
        else:
            route.fulfill(status=204)

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
        )
        context.add_cookies(
            [{"name": "cookie_consent", "value": "false", "url": "https://testserver"}]
        )
        context.route("**/*", serve)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto("https://testserver" + page_path)
            yield page, errors, submitted
        finally:
            context.close()
            browser.close()


def _form_path(kind: str, club: Club, tournament: Tournament | None = None) -> str:
    """Выбрать настоящую страницу создания или редактирования турнира."""
    if kind == "club":
        return reverse(
            "clubs:tournament_edit" if tournament else "clubs:tournament_create",
            kwargs=(
                {"slug": club.slug, "tournament_id": tournament.pk}
                if tournament
                else {"slug": club.slug}
            ),
        )
    model_name = "tvdtournament" if kind == "groups" else "tournament"
    return reverse(
        f"admin:tournaments_{model_name}_{'change' if tournament else 'add'}",
        args=[tournament.pk] if tournament else None,
    )


@pytest.mark.parametrize("kind", ["club", "admin"])
@pytest.mark.parametrize("generated", [False, True], ids=["recruiting", "started"])
def test_tournament_duration_browser_fill_edit_preserves_explicit_dates(
    client: Client, managed_club: Club, kind: str, generated: bool
) -> None:
    """Загрузка, переключение режима и реальный POST не обнуляют сохранённые даты."""
    start = timezone.localdate() + timedelta(days=7)
    end = start + timedelta(days=1)
    deadline = timezone.make_aware(
        datetime.combine(start - timedelta(days=1), time(18, 30))
    )
    tournament = make_tournament(
        club=managed_club if kind == "club" else None,
        city=managed_club.city,
        duration=TournamentDuration.WEEKEND,
        start_date=start,
        end_date=end,
        registration_deadline=deadline,
        start_after_fill=True,
        bracket_generated=generated,
        entry_fee=1000,
        min_participants=4,
        max_participants=8,
    )
    TournamentAllowedCategory.objects.create(tournament=tournament, category="amateur")
    page_path = _form_path(kind, managed_club, tournament)
    document = client.get(page_path, secure=True)
    assert document.status_code == 200
    i18n_document = client.get(reverse("admin:jsi18n"), secure=True).content
    date_format = "%Y-%m-%d" if kind == "club" else "%d.%m.%Y"

    with _browser_page(document.content, page_path, i18n_document) as (
        page,
        errors,
        submitted,
    ):
        expect(page.locator("#id_start_date")).to_have_js_property("readOnly", True)
        expect(page.locator("#id_start_date")).to_have_value(
            start.strftime(date_format)
        )
        expect(page.locator("#id_end_date")).to_have_value(end.strftime(date_format))
        if kind == "club":
            expect(page.locator("#id_registration_deadline")).to_have_value(
                timezone.localtime(deadline).strftime("%Y-%m-%dT%H:%M")
            )
        else:
            expect(page.locator("#id_registration_deadline_0")).to_have_value(
                timezone.localtime(deadline).strftime(date_format)
            )
            expect(page.locator("#id_registration_deadline_1")).to_have_value(
                "18:30:00"
            )
        if not generated:
            page.locator("#id_start_after_fill").uncheck()
            page.locator("#id_start_after_fill").check()
            expect(page.locator("#id_start_date")).to_have_value(
                start.strftime(date_format)
            )
            expect(page.locator("#id_end_date")).to_have_value(
                end.strftime(date_format)
            )
        page.locator("#id_name").fill("Новое название без изменения календаря")
        with page.expect_navigation():
            if kind == "club":
                page.get_by_role(
                    "button", name="Сохранить изменения", exact=True
                ).click()
            else:
                page.locator('[name="_save"]').click()
        assert not errors

    assert len(submitted) == 1
    body, content_type = submitted[0]
    saved = client.generic(
        "POST", page_path, data=body, content_type=content_type, secure=True
    )
    assert saved.status_code == 302, saved.content.decode()
    tournament.refresh_from_db()
    assert tournament.name == "Новое название без изменения календаря"
    assert (
        tournament.start_date,
        tournament.end_date,
        tournament.registration_deadline,
    ) == (start, end, deadline)
    assert tournament.duration == TournamentDuration.WEEKEND


def _submit_tournament_form(page: Page, kind: str) -> None:
    """Отправить форму её настоящей кнопкой, не обходя браузерную валидацию."""
    with page.expect_navigation():
        if kind == "club":
            page.get_by_role("button", name="Сохранить изменения", exact=True).click()
        else:
            page.locator('[name="_save"]').click()


@pytest.mark.parametrize("kind", ["club", "admin", "groups"])
@pytest.mark.parametrize("start_after_fill", [False, True])
def test_tournament_duration_browser_requires_and_saves_single_day_time(
    client: Client, managed_club: Club, kind: str, start_after_fill: bool
) -> None:
    """Все формы требуют время независимо от автозапуска и сохраняют его для сайта."""
    start = timezone.localdate() + timedelta(days=7)
    tournament = make_tournament(
        club=managed_club if kind == "club" else None,
        city=managed_club.city,
        format=(
            TournamentFormat.WEEKEND_DAY
            if kind == "groups"
            else TournamentFormat.SINGLE_ELIMINATION
        ),
        duration=TournamentDuration.SINGLE_DAY,
        start_date=start,
        end_date=start,
        start_time=time(10, 15),
        start_after_fill=start_after_fill,
        entry_fee=1000,
        min_participants=4,
        max_participants=8,
    )
    tournament.allowed_categories.create(category="amateur")
    page_path = _form_path(kind, managed_club, tournament)
    document = client.get(page_path, secure=True)
    assert document.status_code == 200
    i18n_document = client.get(reverse("admin:jsi18n"), secure=True).content
    before = Tournament.objects.filter(pk=tournament.pk).values().get()

    with _browser_page(document.content, page_path, i18n_document) as (
        page,
        errors,
        submitted,
    ):
        start_time = page.locator("#id_start_time")
        expect(start_time).to_be_visible()
        expect(start_time).to_have_attribute("type", "time")
        expect(start_time).to_have_value("10:15")
        expect(start_time).to_have_js_property("required", True)
        page.locator("#id_duration").select_option(TournamentDuration.WEEKEND)
        expect(start_time).to_have_js_property("required", False)
        page.locator("#id_duration").select_option(TournamentDuration.SINGLE_DAY)
        expect(start_time).to_have_js_property("required", True)
        expect(start_time).to_have_attribute("aria-required", "true")
        start_time.fill("")
        assert start_time.evaluate("input => input.validity.valueMissing") is True
        if kind == "club":
            page.get_by_role("button", name="Сохранить изменения", exact=True).click()
            expect(start_time).to_be_focused()
            assert not submitted
            start_time.fill("17:45")
            _submit_tournament_form(page, kind)
        else:
            # Django admin намеренно использует novalidate: ошибка должна
            # вернуться с сервера, а не блокироваться изменённым тестовым DOM.
            expect(
                page.locator("#id_start_time").locator("xpath=ancestor::form")
            ).to_have_js_property("noValidate", True)
            _submit_tournament_form(page, kind)
        assert not errors

    if kind != "club":
        assert len(submitted) == 1
        body, content_type = submitted[0]
        failed = client.generic(
            "POST", page_path, data=body, content_type=content_type, secure=True
        )
        assert failed.status_code == 200
        time_errors = failed.context["adminform"].form.errors.as_data()["start_time"]
        assert any(error.code == "required" for error in time_errors)
        assert Tournament.objects.filter(pk=tournament.pk).values().get() == before
        with _browser_page(failed.content, page_path, i18n_document) as (
            page,
            errors,
            submitted,
        ):
            expect(page.locator(".field-start_time .errorlist")).to_be_visible()
            page.locator("#id_start_time").fill("17:45")
            _submit_tournament_form(page, kind)
            assert not errors

    assert len(submitted) == 1
    body, content_type = submitted[0]
    saved = client.generic(
        "POST", page_path, data=body, content_type=content_type, secure=True
    )
    assert saved.status_code == 302, saved.content.decode()
    tournament.refresh_from_db()
    assert tournament.start_time == time(17, 45)
    assert tournament.start_after_fill is start_after_fill
    assert (tournament.start_date, tournament.end_date) == (start, start)
    detail = client.get(
        reverse("tournament_detail", kwargs={"slug": tournament.slug}), secure=True
    )
    assert detail.status_code == 200
    assert "17:45" in detail.content.decode()


@pytest.mark.parametrize("kind", ["club", "admin", "groups"])
def test_tournament_duration_browser_finds_and_saves_court_by_name(
    client: Client, managed_club: Club, kind: str
) -> None:
    """Название ищется в настоящем endpoint, а выбранный ID проходит реальное сохранение."""
    court = Court.objects.create(
        name="Корт Изумруд",
        slug="emerald-court",
        city=managed_club.city,
        address="Теннисная улица, 7",
        is_active=True,
    )
    Court.objects.create(
        name="Другая площадка",
        slug="unrelated-court",
        city=managed_club.city,
        address="Спортивная улица, 8",
        is_active=True,
    )
    start = timezone.localdate() + timedelta(days=7)
    tournament = make_tournament(
        club=managed_club if kind == "club" else None,
        city=managed_club.city,
        format=(
            TournamentFormat.WEEKEND_DAY
            if kind == "groups"
            else TournamentFormat.SINGLE_ELIMINATION
        ),
        start_date=start,
        entry_fee=1000,
        min_participants=4,
        max_participants=8,
    )
    tournament.allowed_categories.create(category="amateur")
    page_path = _form_path(kind, managed_club, tournament)
    document = client.get(page_path, secure=True)
    assert document.status_code == 200
    i18n_document = client.get(reverse("admin:jsi18n"), secure=True).content
    needle = "Изумруд"
    if kind == "club":
        search_path = reverse(
            "clubs:tournament_court_search", kwargs={"slug": managed_club.slug}
        )
        search_data = {"q": needle, "sport": "tennis"}
    else:
        search_path = reverse("admin:autocomplete")
        search_data = {
            "term": needle,
            "app_label": "tournaments",
            "model_name": "tvdtournament" if kind == "groups" else "tournament",
            "field_name": "court",
            "page": "1",
        }
    search = client.get(search_path, search_data, secure=True)
    assert search.status_code == 200
    assert [str(row["id"]) for row in search.json()["results"]] == [str(court.pk)]

    with _browser_page(
        document.content,
        page_path,
        i18n_document,
        ajax_documents={(search_path, needle): search.content},
    ) as (page, errors, submitted):
        if kind == "club":
            query = page.locator("[data-court-query]")
            selected = page.locator("[data-court-value]")
            query.fill(needle)
            expect(
                page.get_by_role("option").filter(has_text=court.name)
            ).to_be_visible()
            query.press("ArrowDown")
            query.press("Enter")
            expect(selected).to_have_value(str(court.pk))
            page.get_by_role("button", name="Сбросить", exact=True).click()
            expect(selected).to_have_value("")
            expect(query).to_be_focused()
            query.fill(needle)
        else:
            page.locator(".field-court .select2-selection").click()
            page.locator(".select2-container--open .select2-search__field").fill(needle)
            selected = page.locator("#id_court")
        page.get_by_role("option").filter(has_text=court.name).click()
        expect(selected).to_have_value(str(court.pk))
        _submit_tournament_form(page, kind)
        assert not errors

    assert len(submitted) == 1
    body, content_type = submitted[0]
    saved = client.generic(
        "POST", page_path, data=body, content_type=content_type, secure=True
    )
    assert saved.status_code == 302, saved.content.decode()
    tournament.refresh_from_db()
    assert tournament.court_id == court.pk


@pytest.mark.parametrize("action", ["extend", "reopen"])
def test_tournament_duration_browser_lifecycle_error_focuses_summary_and_can_be_fixed(
    client: Client, managed_club: Club, action: str
) -> None:
    """Серверный отказ сохраняет ввод, фокусирует сводку и позволяет исправить календарь."""
    start = timezone.localdate() + timedelta(days=7)
    tournament = make_tournament(
        start_date=start,
        end_date=start,
        start_time=time(11, 30),
        duration=TournamentDuration.SINGLE_DAY,
        entry_fee=1000,
        status="cancelled" if action == "reopen" else "upcoming",
    )
    before = Tournament.objects.filter(pk=tournament.pk).values().get()
    page_path = reverse("tournament_manage", kwargs={"slug": tournament.slug})
    action_path = reverse(
        (
            "tournament_manage_reopen"
            if action == "reopen"
            else "tournament_manage_extend_registration"
        ),
        kwargs={"slug": tournament.slug},
    )
    document = client.get(page_path, secure=True)
    assert document.status_code == 200
    new_start = start + timedelta(days=3)
    label = "Возобновить набор" if action == "reopen" else "Продлить регистрацию"
    start_date_selector = f"#{action}-start_date"
    start_time_selector = f"#{action}-start_time"
    deadline_selector = f"#{action}-registration_deadline"

    with _browser_page(document.content, page_path, b"", post_path=action_path) as (
        page,
        errors,
        submitted,
    ):
        page.locator(start_date_selector).fill(new_start.isoformat())
        page.locator(start_time_selector).fill("17:30")
        page.locator(deadline_selector).fill(f"{new_start.isoformat()}T18:00")
        with page.expect_navigation():
            page.get_by_role("button", name=label, exact=True).click()
        assert not errors
    assert len(submitted) == 1
    body, content_type = submitted[0]
    failed = client.generic(
        "POST", action_path, data=body, content_type=content_type, secure=True
    )
    assert failed.status_code == 200
    assert Tournament.objects.filter(pk=tournament.pk).values().get() == before

    with _browser_page(failed.content, action_path, b"") as (page, errors, corrected):
        summary = page.locator("#lifecycle-errors")
        expect(summary).to_be_visible()
        expect(summary).to_be_focused()
        expect(summary).to_have_attribute("role", "alert")
        expect(page.locator(start_date_selector)).to_have_value(new_start.isoformat())
        expect(page.locator(start_time_selector)).to_have_value("17:30")
        expect(page.locator(deadline_selector)).to_have_value(
            f"{new_start.isoformat()}T18:00"
        )
        summary.locator(f'a[href="{deadline_selector}"]').click()
        expect(page.locator(deadline_selector)).to_be_focused()
        page.locator(deadline_selector).fill(f"{new_start.isoformat()}T17:00")
        with page.expect_navigation():
            page.get_by_role("button", name=label, exact=True).click()
        assert not errors
    assert len(corrected) == 1
    body, content_type = corrected[0]
    saved = client.generic(
        "POST", action_path, data=body, content_type=content_type, secure=True
    )
    assert saved.status_code == 302, saved.content.decode()
    tournament.refresh_from_db()
    assert tournament.status == "upcoming"
    assert (tournament.start_date, tournament.end_date) == (new_start, new_start)
    assert tournament.start_time == time(17, 30)


def _browser_registration_player() -> Player:
    """Создать подтверждённый профиль обычного игрока для реальных форм регистрации."""
    player = make_player(
        email_suffix="browser-entry",
        first_name="Иван",
        last_name="Игрок",
        email_verified=True,
        phone="+79990000000",
    )
    player.birth_date = date(1990, 1, 1)
    player.gender = "male"
    player.is_verified = True
    player.tennis_entry_level_set = True
    player.skill_level = "amateur"
    player.save(
        update_fields=[
            "birth_date",
            "gender",
            "is_verified",
            "tennis_entry_level_set",
            "skill_level",
        ]
    )
    return player


@pytest.mark.parametrize("doubles", [False, True])
@pytest.mark.parametrize("method", ["onsite", "postpayment"])
def test_tournament_duration_browser_payment_choice_requires_explicit_submission(
    client: Client, doubles: bool, method: str
) -> None:
    """Оба способа доступны, GET и неполная форма не регистрируют и не списывают FT."""
    player = _browser_registration_player()
    subscription = make_subscription(player.user, fancoin_balance=10)
    client.force_login(player.user)
    tournament = make_tournament(
        start_date=timezone.localdate() + timedelta(days=7),
        entry_fee=1000,
        allow_onsite_payment=True,
        allow_postpayment=True,
        variant="doubles" if doubles else "singles",
    )
    tournament.allowed_categories.create(category="amateur")
    page_path = reverse(
        "tournament_register_doubles" if doubles else "tournament_register",
        kwargs={"slug": tournament.slug},
    )
    document = client.get(page_path, secure=True)
    assert document.status_code == 200
    assert not tournament.participants.exists()
    assert not tournament.teams.exists()
    assert not tournament.onsite_payments.exists()
    assert not PaymentRecord.objects.exists()

    with _browser_page(document.content, page_path, b"") as (page, errors, submitted):
        form = (
            page.locator("form").filter(
                has=page.locator('[name="action"][value="solo"]')
            )
            if doubles
            else page.locator("form").filter(
                has=page.locator('[name="payment_method"]')
            )
        )
        expect(
            form.locator('[name="payment_method"][value="onsite"]')
        ).not_to_be_checked()
        expect(
            form.locator('[name="payment_method"][value="postpayment"]')
        ).not_to_be_checked()
        button = form.get_by_role(
            "button",
            name=(
                "Зарегистрироваться без партнёра"
                if doubles
                else "Продолжить регистрацию"
            ),
            exact=True,
        )
        button.click()
        assert not submitted
        form.locator(f'[name="payment_method"][value="{method}"]').check()
        with page.expect_navigation():
            button.click()
        assert not errors
    assert len(submitted) == 1
    body, content_type = submitted[0]
    response = client.generic(
        "POST", page_path, data=body, content_type=content_type, secure=True
    )
    assert response.status_code == 302, response.content.decode()
    if doubles:
        assert tournament.teams.get().player1_id == player.pk
    else:
        assert tournament.participants.filter(pk=player.pk).exists()
    assert tournament.onsite_payments.count() == (1 if method == "onsite" else 0)
    if method == "onsite":
        onsite = tournament.onsite_payments.get()
        assert onsite.status == "expected"
        assert onsite.received_at is None
    subscription.refresh_from_db()
    assert subscription.fancoin_balance == 10
    assert not tournament.entry_payments.exists()
    assert not PaymentRecord.objects.exists()


@pytest.mark.parametrize("kind", ["club", "admin", "groups"])
def test_tournament_duration_browser_format_and_duration_are_independent(
    client: Client, managed_club: Club, kind: str
) -> None:
    """Форма оставляет календарь доступным при любой сетке и не меняет продолжительность."""
    page_path = _form_path(kind, managed_club)
    document = client.get(page_path, secure=True)
    assert document.status_code == 200
    i18n_document = client.get(reverse("admin:jsi18n"), secure=True).content

    with _browser_page(document.content, page_path, i18n_document) as (page, errors, _):
        expect(page.locator('[name="is_one_day"]')).to_have_count(0)
        formats = (
            [TournamentFormat.WEEKEND_DAY]
            if kind == "groups"
            else TournamentFormat.values
        )
        for tournament_format in formats:
            if kind != "groups":
                page.locator("#id_format").select_option(tournament_format)
            for duration in TournamentDuration.values:
                page.locator("#id_duration").select_option(duration)
                expect(page.locator("#id_duration")).to_be_visible()
                expect(page.locator("#id_start_date")).to_be_visible()
                expect(page.locator("#id_end_date")).to_be_visible()
                expect(page.locator("#id_allow_postpayment")).to_be_enabled()
                page.locator("#id_allow_postpayment").check()
                if duration == TournamentDuration.MULTI_DAY:
                    expect(page.locator("#id_match_days_per_round")).to_be_visible()
                else:
                    expect(page.locator("#id_match_days_per_round")).to_be_hidden()
                if kind != "groups":
                    page.locator("#id_format").select_option(tournament_format)
                expect(page.locator("#id_duration")).to_have_value(duration)
                expect(page.locator("#id_allow_postpayment")).to_be_checked()
        assert not errors


def test_tournament_duration_browser_club_shows_legacy_end_warning(
    client: Client, managed_club: Club
) -> None:
    """Предупреждение о старой дате видно и связано с полем окончания."""
    start = timezone.localdate() + timedelta(days=7)
    tournament = make_tournament(
        club=managed_club, city=managed_club.city, start_date=start, entry_fee=1000
    )
    Tournament.objects.filter(pk=tournament.pk).update(
        end_date=start - timedelta(days=1)
    )
    page_path = _form_path("club", managed_club, tournament)
    document = client.get(page_path, secure=True)
    assert document.status_code == 200
    i18n_document = client.get(reverse("admin:jsi18n"), secure=True).content

    with _browser_page(document.content, page_path, i18n_document) as (page, errors, _):
        warning = page.locator(
            '[data-fill-date-field="end_date"] .club-tournament-create__help'
        )
        expect(warning).to_be_visible()
        expect(warning).to_contain_text("Существующее значение")
        expect(page.locator("#id_end_date")).to_have_attribute(
            "aria-describedby", "id_end_date_helptext"
        )
        expect(warning).to_have_attribute("id", "id_end_date_helptext")
        assert not errors
