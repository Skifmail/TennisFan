"""Браузерная приёмка общей ленты без доступа к рабочему сайту."""

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
from tests.support.factories import make_player

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not (environ.get("CHAT_BROWSER_EXECUTABLE") or environ.get("CHAT_E2E_URL")),
        reason="Браузерная проверка запускается на отдельном этапе CI",
    ),
]


def test_feed_when_browsing_desktop_and_mobile_should_group_filter_and_paginate(
    client: Client,
) -> None:
    """Реальный браузер проверяет группы, фильтр, пагинацию и ширину экрана.

    Args:
        client: Клиент Django для изолированного рендеринга страниц.
    """
    player = make_player(
        email_suffix="feed-browser", first_name="Алексей", last_name="Игрок"
    )
    Player.objects.filter(pk=player.pk).update(city="Раменское", is_verified=True)
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

    documents = {
        (kind, number): client.get(
            "/sparring/", {"type": kind, "page": number}, secure=True
        ).content
        for kind in ("all", "singles", "doubles", "team")
        for number in ("1", "2")
    }

    def serve(route: Route) -> None:
        url = urlsplit(route.request.url)
        if url.netloc != "testserver":
            route.abort()
            return
        if url.path.startswith("/static/"):
            found = finders.find(url.path.removeprefix("/static/"))
            if isinstance(found, str):
                route.fulfill(
                    body=Path(found).read_bytes(),
                    content_type=guess_type(found)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404)
            return
        if url.path != "/sparring/":
            route.fulfill(status=204)
            return
        params = parse_qs(url.query)
        key = (params.get("type", ["all"])[0], params.get("page", ["1"])[0])
        route.fulfill(body=documents[key], content_type="text/html; charset=utf-8")

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/*", serve)
        try:
            page.goto("https://testserver/sparring/")
            expect(
                page.get_by_role("heading", name="Рядом с вами", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_role("heading", name="В других городах", exact=True)
            ).to_have_count(1)
            expect(page.locator("#sparring-feed .sparring-card")).to_have_count(10)
            expect(
                page.get_by_role("link", name="Все форматы", exact=True)
            ).to_have_class("sparring-tab sparring-tab--active")
            for width in (1440, 900, 390, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                radius = page.locator(".sparring-radius")
                expect(radius).to_have_count(1)
                expect(
                    radius.get_by_role("link", name="10 км", exact=True)
                ).not_to_be_visible()
                toolbar_height = page.locator(".sparring-toolbar__panel").bounding_box()
                radius.locator("summary").click()
                expect(
                    radius.get_by_role("link", name="10 км", exact=True)
                ).to_be_visible()
                menu = radius.locator(".sparring-radius__menu").bounding_box()
                after_open = page.locator(".sparring-toolbar__panel").bounding_box()
                assert (
                    menu is not None
                    and toolbar_height is not None
                    and after_open is not None
                )
                assert menu["x"] >= 0 and menu["x"] + menu["width"] <= width
                assert toolbar_height["height"] == after_open["height"]
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                radius.locator("summary").click()
                for tab in page.locator(
                    '[aria-label="Формат спарринга"] .sparring-tab'
                ).all():
                    rectangle = tab.bounding_box()
                    assert rectangle is not None
                    assert rectangle["x"] >= 0
                    assert rectangle["x"] + rectangle["width"] <= width
                if width <= 390:
                    rows = [
                        tab.bounding_box()
                        for tab in page.locator(
                            '[aria-label="Формат спарринга"] .sparring-tab'
                        ).all()
                    ]
                    assert rows[0] is not None and rows[1] is not None
                    assert rows[2] is not None and rows[3] is not None
                    assert rows[0]["y"] == rows[1]["y"]
                    assert rows[2]["y"] == rows[3]["y"]
                    assert rows[2]["y"] > rows[0]["y"]
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.screenshot(
                path="/tmp/tennison-sparring-feed-desktop.png", animations="disabled"
            )
            page.set_viewport_size({"width": 390, "height": 1000})
            page.screenshot(
                path="/tmp/tennison-sparring-feed-mobile.png", animations="disabled"
            )
            page.locator(".sparring-toolbar__panel").screenshot(
                path="/tmp/tennison-sparring-toolbar-compact.png", animations="disabled"
            )
            other_section = page.locator(".sparring-feed__section--other")
            other_section.scroll_into_view_if_needed()
            expect(other_section.locator(".sparring-card").first).to_have_css(
                "opacity", "1"
            )
            page.screenshot(
                path="/tmp/tennison-sparring-feed-other-mobile.png",
                animations="disabled",
            )
            page.get_by_role("link", name="Дальше", exact=True).click()
            expect(page.locator("#sparring-feed .sparring-card")).to_have_count(1)
            expect(
                page.get_by_role("heading", name="В других городах", exact=True)
            ).to_have_count(1)
            page.get_by_role("link", name="Одиночный 1×1", exact=True).click()
            expect(page.locator("#sparring-feed .sparring-card")).to_have_count(7)
            expect(page.locator("#sparring-feed .sparring-card__roster")).to_have_count(
                0
            )
            page.get_by_role("link", name="Все форматы", exact=True).click()
            expect(page.locator("#sparring-feed .sparring-card")).to_have_count(10)
        finally:
            browser.close()
