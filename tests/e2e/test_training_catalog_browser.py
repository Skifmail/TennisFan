"""Браузерная приёмка каталога: адаптивность, формы, доступность и навигация."""

from __future__ import annotations

from mimetypes import guess_type
from os import environ
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from playwright.sync_api import Browser, Route, expect, sync_playwright

from apps.core.geo import GeoRegion
from apps.core.models import GeoArea
from apps.courts.models import Court
from apps.training.models import Training
from tests.support.training_catalog import make_catalog_training

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not environ.get("CHAT_BROWSER_EXECUTABLE"),
        reason="Браузер запускается на отдельном этапе CI",
    ),
]


@pytest.fixture
def catalog_documents(client: Client) -> dict[str, bytes]:
    """Подготовить HTML до запуска Playwright, не вызывая ORM из async-цикла."""
    make_catalog_training()
    Training.objects.create(
        title="Индивидуальная тренировка",
        slug="catalog-simple",
        description="Подача и техника ударов.",
        city="Москва",
        type_prices={"individual": 2500},
        skill_levels=["novice"],
    )
    Training.objects.create(
        title="НазваниеБезПробелов" * 10,
        slug="catalog-long",
        description="ДлинноеОписаниеБезПробелов" * 10,
        city="ОченьДлинныйГородБезПробелов" * 3,
    )
    area = GeoArea.objects.get(slug="yug")
    for name, slug in (
        ("Южный корт", "catalog-court-south"),
        ("Арена", "catalog-arena"),
    ):
        Court.objects.create(
            name=name,
            slug=slug,
            city="Москва",
            address="ул. Тестовая, 1",
            surface="хард",
            region=GeoRegion.MOSCOW,
            geo_area=area,
        )
    documents: dict[str, bytes] = {}
    for key, params in (
        ("/training/", {}),
        ("/training/?area=yug", {"area": "yug"}),
        ("/training/?city=Казань", {"city": "Казань"}),
    ):
        response = client.get("/training/", params, secure=True)
        assert response.status_code == 200, (
            key,
            response.status_code,
            response.content[:500],
        )
        assert b"data-training-filters" in response.content, (
            key,
            response.content[:500],
        )
        documents[key] = response.content
    return documents


def route_catalog(browser: Browser, documents: dict[str, bytes]) -> None:
    """Отдать настоящие шаблоны и статику; не обращаться к рабочему сайту."""
    browser.contexts[0].add_cookies(
        [{"name": "cookie_consent", "value": "false", "url": "https://testserver"}]
    )

    def serve(route: Route) -> None:
        """Разрешить только тестовый домен, локальную статику и подготовленные страницы."""
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
        key = url.path + ("?" + unquote(url.query) if url.query else "")
        body = documents.get(
            key, "<html lang='ru'><body>Открыто</body></html>".encode()
        )
        route.fulfill(body=body, content_type="text/html; charset=utf-8")

    browser.contexts[0].route("**/*", serve)


@pytest.mark.parametrize(
    "width", [320, 375, 390, 600, 699, 700, 768, 899, 900, 1024, 1199, 1200, 1440]
)
@pytest.mark.parametrize("reduced", [False, True])
def test_catalog_layout_and_information(
    catalog_documents: dict[str, bytes], width: int, reduced: bool
) -> None:
    """Длинные сведения не обрезаются, поля и карточки помещаются на всех ширинах."""
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ["CHAT_BROWSER_EXECUTABLE"]
        )
        page = browser.new_page(
            viewport={"width": width, "height": 900},
            reduced_motion="reduce" if reduced else "no-preference",
        )
        route_catalog(browser, catalog_documents)
        try:
            page.goto("https://testserver/training/")
            page.evaluate("document.fonts.ready")
            expect(page.locator(".training-offer")).to_have_count(3)
            expect(
                page.get_by_role("button", name="Показать тренировки")
            ).to_be_visible()
            expect(
                page.locator(".training-offer__facts").get_by_text("90 мин")
            ).to_be_visible()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), page.evaluate(
                """({widths: [document.documentElement.scrollWidth, innerWidth, document.body.scrollWidth], elements: [...document.querySelectorAll('body *')].filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1);
                }).map(el => [el.className, el.getBoundingClientRect().width]).slice(0, 20)})"""
            )
            assert page.locator(".training-catalog").evaluate(
                """root => [...root.querySelectorAll('input, select, button, .training-catalog__area')]
                    .filter(el => el.type !== 'hidden')
                    .every(el => el.getBoundingClientRect().height >= 44)"""
            )
            assert page.locator(".training-catalog").evaluate(
                """root => [...root.querySelectorAll('p, dd, h3, .training-offer__footer')]
                    .every(el => el.scrollWidth <= el.clientWidth + 1)"""
            )
            columns = page.locator(".training-catalog__grid").evaluate(
                "el => getComputedStyle(el).gridTemplateColumns.split(' ').length"
            )
            assert columns == (3 if width >= 1200 else 2 if width >= 700 else 1)
            if width in (390, 768, 1440) and not reduced:
                directory = environ.get("TRAINING_CATALOG_SCREENSHOTS")
                if directory:
                    path = Path(directory)
                    path.mkdir(parents=True, exist_ok=True)
                    page.locator(".training-catalog").screenshot(
                        path=str(path / f"catalog-{width}.png")
                    )
        finally:
            browser.close()


@pytest.mark.parametrize("javascript", [True, False])
def test_catalog_form_does_not_interrupt_selection(
    catalog_documents: dict[str, bytes], javascript: bool
) -> None:
    """Несколько фильтров применяются одним GET, без неожиданных отправок при выборе."""
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ["CHAT_BROWSER_EXECUTABLE"]
        )
        page = browser.new_page(
            viewport={"width": 390, "height": 900}, java_script_enabled=javascript
        )
        route_catalog(browser, catalog_documents)
        try:
            page.goto("https://testserver/training/?area=yug")
            expect(page.get_by_label("Тип", exact=True)).to_be_visible()
            page.get_by_label("Тип", exact=True).select_option("group")
            page.get_by_label("Уровень", exact=True).select_option("novice")
            page.get_by_label("Город", exact=True).fill("Раменское")
            assert page.url == "https://testserver/training/?area=yug"
            page.get_by_role("button", name="Показать тренировки").click()
            expect(page.get_by_text("Открыто", exact=True)).to_be_visible()
            params = parse_qs(urlsplit(page.url).query)
            assert params["city"] == ["Раменское"]
            assert params["type"] == ["group"]
            assert params["level"] == ["novice"]
            if javascript:
                assert "area" not in params
        finally:
            browser.close()


def test_catalog_courts_keyboard_focus_and_stretched_link(
    catalog_documents: dict[str, bytes],
) -> None:
    """Нативное раскрытие кортов, поиск, клавиатурный фокус и клик по фото работают."""
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ["CHAT_BROWSER_EXECUTABLE"]
        )
        page = browser.new_page(viewport={"width": 768, "height": 900})
        route_catalog(browser, catalog_documents)
        try:
            page.goto("https://testserver/training/?area=yug")
            page.locator("summary").focus()
            page.keyboard.press("Enter")
            expect(page.get_by_label("Найти корт по названию")).to_be_visible()
            page.get_by_label("Найти корт по названию").fill("южный")
            expect(
                page.get_by_role("link", name="Южный корт", exact=True)
            ).to_be_visible()
            expect(page.get_by_role("link", name="Арена", exact=True)).to_be_hidden()
            page.get_by_label("Найти корт по названию").fill("нет совпадений")
            expect(page.get_by_text("Ничего не найдено.", exact=True)).to_be_visible()
            offer = page.locator(".training-offer").first
            offer.locator("h3 a").focus()
            assert offer.evaluate("el => getComputedStyle(el).outlineStyle") == "solid"
            target = offer.locator("h3 a").get_attribute("href")
            media = offer.locator(".training-offer__media")
            media.scroll_into_view_if_needed()
            bounds = media.bounding_box()
            assert bounds is not None
            # Кликаем как пользователь: ссылку намеренно растянули поверх фотографии.
            page.mouse.click(
                bounds["x"] + bounds["width"] / 2,
                bounds["y"] + bounds["height"] / 2,
            )
            expect(page).to_have_url("https://testserver" + str(target))
        finally:
            browser.close()


def test_catalog_area_keeps_draft_filters(catalog_documents: dict[str, bytes]) -> None:
    """Район применяет текущие поля, включая ещё не отправленный выбор спорта."""
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ["CHAT_BROWSER_EXECUTABLE"]
        )
        page = browser.new_page(viewport={"width": 390, "height": 900})
        route_catalog(browser, catalog_documents)
        try:
            page.goto("https://testserver/training/")
            page.get_by_label("Вид спорта", exact=True).select_option("all")
            page.get_by_label("Тип", exact=True).select_option("group")
            page.get_by_label("Уровень", exact=True).select_option("novice")
            assert page.url == "https://testserver/training/"
            page.get_by_role("link", name="Юг", exact=True).click()
            expect(page.get_by_text("Открыто", exact=True)).to_be_visible()
            params = parse_qs(urlsplit(page.url).query)
            assert params == {
                "city": ["Москва"],
                "area": ["yug"],
                "sport": ["all"],
                "type": ["group"],
                "level": ["novice"],
            }
        finally:
            browser.close()


@pytest.mark.parametrize("javascript", [True, False])
def test_catalog_enter_empty_state_and_reset(
    catalog_documents: dict[str, bytes], javascript: bool
) -> None:
    """Enter отправляет форму; пустая выдача и сброс работают без JavaScript."""
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ["CHAT_BROWSER_EXECUTABLE"]
        )
        page = browser.new_page(java_script_enabled=javascript)
        route_catalog(browser, catalog_documents)
        try:
            page.goto("https://testserver/training/")
            page.get_by_label("Город", exact=True).fill("Казань")
            page.get_by_label("Город", exact=True).press("Enter")
            expect(page.get_by_text("Открыто", exact=True)).to_be_visible()
            assert parse_qs(urlsplit(page.url).query)["city"] == ["Казань"]
            page.goto("https://testserver/training/?city=Казань")
            expect(
                page.get_by_text("По этим условиям ничего не найдено")
            ).to_be_visible()
            expect(page.locator(".training-catalog__count")).to_have_text("Найдено: 0")
            page.get_by_role("link", name="Сбросить фильтры", exact=True).click()
            expect(page.locator(".training-offer")).to_have_count(3)
            expect(page.get_by_label("Город", exact=True)).to_have_value("")
        finally:
            browser.close()
