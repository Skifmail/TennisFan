"""Браузерная приёмка компактного меню и общей ленты без production."""

from itertools import combinations
from mimetypes import guess_type
from os import environ
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from playwright.sync_api import Route, expect, sync_playwright

from apps.core.models import City
from apps.sparring.models import DoublesMatchRequest, SparringRequest
from apps.users.models import Player
from tests.support.factories import make_player, make_subscription

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not (environ.get("CHAT_BROWSER_EXECUTABLE") or environ.get("CHAT_E2E_URL")),
        reason="Браузер запускается на отдельном этапе CI",
    ),
]


@pytest.mark.parametrize("paid", [False, True])
def test_compact_menu_when_desktop_or_mobile_should_support_multi_select_and_actions(
    client: Client,
    paid: bool,
) -> None:
    """Меню компактно, доступно с клавиатуры и сохраняет общую пагинацию.

    Args:
        client: Клиент изолированной тестовой базы.
        paid: Есть ли у зрителя доступ к созданию заявок.
    """
    player = make_player(
        email_suffix="compact-browser", first_name="Алексей", last_name="Игрок"
    )
    Player.objects.filter(pk=player.pk).update(city="Раменское", is_verified=True)
    if paid:
        make_subscription(
            player.user, tier_name="compact-browser", tier_kwargs={"has_sparring": True}
        )
    client.force_login(player.user)
    City.objects.get_or_create(
        name="Жуковский",
        region="Московская область",
        defaults={"lat": 55.6, "lng": 38.1},
    )
    for index in range(7):
        SparringRequest.objects.create(
            player=player, city="Раменское", description=f"Местная заявка {index}"
        )
    for index in range(4):
        DoublesMatchRequest.objects.create(
            created_by=player, city="Жуковский", kind="team" if index % 2 else "classic"
        )
    formats = ("singles", "doubles", "team")
    selections = [
        (),
        *[choice for size in (1, 2) for choice in combinations(formats, size)],
    ]
    documents = {
        (choice, number, sport): client.get(
            "/sparring/",
            {"type": list(choice) or ["all"], "page": number, "sport": sport},
            secure=True,
        ).content
        for choice in selections
        for number in ("1", "2")
        for sport in ("tennis", "padel")
    }

    def serve(route: Route) -> None:
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
        elif url.path == "/sparring/":
            params = parse_qs(url.query)
            raw = params.get("type", [])
            choice = tuple(code for code in formats if code in raw)
            if len(choice) == len(formats) or "all" in raw:
                choice = ()
            key = (
                choice,
                params.get("page", ["1"])[0],
                params.get("sport", ["tennis"])[0],
            )
            route.fulfill(body=documents[key], content_type="text/html; charset=utf-8")
        else:
            route.fulfill(status=204)

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/*", serve)
        try:
            page.goto("https://testserver/sparring/")
            expect(page.locator("#sparring-feed .sparring-card")).to_have_count(10)
            page.locator(".sparring-compact").screenshot(
                path="/tmp/tennison-compact-v2-desktop.png"
            )
            for width in (1440, 900, 390, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                toolbar = page.locator(".sparring-compact")
                rectangle = toolbar.bounding_box()
                assert rectangle is not None and rectangle["height"] <= 120
                controls = page.locator(
                    ".sparring-compact__row > a, .sparring-compact__formats > summary"
                ).all()
                positions = [control.bounding_box() for control in controls]
                assert len(positions) == 3 and all(
                    position is not None for position in positions
                )
                assert (
                    len({round(position["y"]) for position in positions if position})
                    == 1
                )
                filters = page.locator(".sparring-compact__filters")
                expect(filters.locator("form")).not_to_be_visible()
                filters.locator("summary").click()
                expect(filters.locator("form")).to_be_visible()
                popup = filters.locator("form").bounding_box()
                assert popup is not None and popup["x"] >= 0
                assert popup["x"] + popup["width"] <= width
                after_open = toolbar.bounding_box()
                assert (
                    after_open is not None
                    and after_open["height"] == rectangle["height"]
                )
                page.keyboard.press("Escape")
                expect(filters.locator("form")).not_to_be_visible()
            page.set_viewport_size({"width": 390, "height": 1000})
            page.get_by_role("heading", name="Спарринг", exact=True).click()
            page.locator(".sparring-compact").screenshot(
                path=f"/tmp/tennison-compact-v2-{'paid' if paid else 'free'}.png"
            )
            mine = page.locator(".sparring-compact__mine")
            mine.locator("summary").click()
            expect(
                mine.get_by_role("link", name="Мои заявки", exact=True)
            ).to_be_visible()
            expect(
                mine.get_by_role("link", name="Пригласить на спарринг", exact=True)
            ).to_have_count(1 if paid else 0)
            page.get_by_role("heading", name="Спарринг", exact=True).click()
            expect(mine.locator("nav")).not_to_be_visible()
            expect(page.locator(".sparring-compact__primary")).to_have_text(
                "Создать заявку" if paid else "Подписка"
            )
            page.get_by_role("link", name="Дальше", exact=True).click()
            expect(page.locator("#sparring-feed .sparring-card")).to_have_count(1)
            formats_menu = page.locator(".sparring-compact__formats")
            formats_menu.locator("summary").click()
            formats_menu.get_by_label("Парный 2×2", exact=True).uncheck()
            formats_menu.locator("form").screenshot(
                path="/tmp/tennison-compact-v2-formats.png"
            )
            formats_menu.get_by_role("button", name="Показать", exact=True).click()
            expect(page.locator("#sparring-feed .sparring-card")).to_have_count(9)
            expect(page.locator(".sparring-compact__formats > summary")).to_have_text(
                "Форматы · 2"
            )
            assert parse_qs(urlsplit(page.url).query)["type"] == ["singles", "team"]
            page.set_viewport_size({"width": 1440, "height": 1000})
            formats_menu.locator("summary").click()
            formats_menu.get_by_label("Одиночный 1×1", exact=True).uncheck()
            formats_menu.get_by_role("button", name="Показать", exact=True).click()
            expect(page.locator("#sparring-feed .sparring-card")).to_have_count(2)
        finally:
            browser.close()
