"""Браузерная проверка географических разделов каталога турниров."""

from mimetypes import guess_type
from os import environ
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from playwright.sync_api import Route, expect, sync_playwright

from apps.core.models import City
from apps.users.models import Player
from tests.support.factories import make_player, make_tournament

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not (environ.get("CHAT_BROWSER_EXECUTABLE") or environ.get("CHAT_E2E_URL")),
        reason="Браузерная проверка выполняется отдельным этапом CI",
    ),
]


def test_tournament_sections_when_desktop_or_mobile_should_show_local_then_other_cities(
    client: Client,
) -> None:
    """Разделы и расстояния видны на компьютере и узком экране без переполнения.

    Args:
        client: Клиент изолированной тестовой базы.
    """
    player = make_player(email_suffix="tournament-local-browser")
    Player.objects.filter(pk=player.pk).update(city="Раменское", is_verified=True)
    client.force_login(player.user)
    City.objects.get_or_create(
        name="Жуковский",
        region="Московская область",
        defaults={"lat": 55.6, "lng": 38.1},
    )
    for name, slug, city in [
        ("Домашний турнир", "home-browser", "Раменское"),
        ("Турнир в Жуковском", "near-browser", "Жуковский"),
        ("Дальний турнир", "far-browser", "Сочи"),
    ]:
        make_tournament(name=name, slug=slug, city=city, status="active")
    document = client.get("/tournaments/", secure=True).content

    def serve(route: Route) -> None:
        url = urlsplit(route.request.url)
        if url.netloc != "testserver":
            route.abort()
        elif url.path.lower().endswith(".mp4"):
            route.fulfill(status=204)
        elif url.path.startswith("/static/"):
            found = finders.find(url.path.removeprefix("/static/"))
            if isinstance(found, str):
                route.fulfill(
                    body=Path(found).read_bytes(),
                    content_type=guess_type(found)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404)
        elif url.path == "/tournaments/":
            route.fulfill(body=document, content_type="text/html; charset=utf-8")
        else:
            route.fulfill(status=204)

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/*", serve)
        try:
            page.goto("https://testserver/tournaments/", wait_until="domcontentloaded")
            for width in (1440, 390, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                sections = page.locator(".tournaments-local-section")
                expect(sections).to_have_count(2)
                expect(
                    sections.nth(0).get_by_role(
                        "heading", name="Рядом с вами", exact=True
                    )
                ).to_have_count(1)
                expect(
                    sections.nth(0).get_by_text("Домашний турнир", exact=True)
                ).to_have_count(1)
                expect(
                    sections.nth(1).get_by_role(
                        "heading", name="В других городах", exact=True
                    )
                ).to_have_count(1)
                titles = (
                    sections.nth(1)
                    .locator(".tournament-card__title")
                    .all_text_contents()
                )
                assert [title.strip() for title in titles] == [
                    "Турнир в Жуковском",
                    "Дальний турнир",
                ]
                if width == 390:
                    sections.nth(0).get_by_role(
                        "heading", name="Рядом с вами", exact=True
                    ).evaluate("element => element.scrollIntoView({block: 'center'})")
                    page.screenshot(
                        path="/tmp/tennison-tournament-local-v2.png",
                        animations="disabled",
                    )
        finally:
            browser.close()
