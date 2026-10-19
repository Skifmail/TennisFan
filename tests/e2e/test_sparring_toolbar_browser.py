"""Браузерная проверка читаемости реальной панели фильтров спарринга."""

from os import environ

import pytest

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен изолированный браузерный стенд"
)


def test_sparring_filters_when_viewport_changes_should_keep_fields_readable_without_overflow() -> (
    None
):
    """На компьютере поля не сжимаются, на телефоне не выходят за экран."""
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        # Стенд не обращается к аналитике, картам и внешним изображениям.
        context.route(
            "**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith(environ["CHAT_E2E_URL"])
                else route.abort()
            ),
        )
        try:
            page = context.new_page()
            page.goto(environ["CHAT_E2E_URL"] + "/sparring/?type=singles&sport=tennis")
            panel = page.locator(".sparring-compact__filter-form")
            page.locator(".sparring-compact__filters > summary").click()
            expect(panel).to_be_visible()
            for width in (1440, 1100, 900, 390, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                desktop_minimum = page.evaluate(
                    "parseFloat(getComputedStyle(document.documentElement).fontSize) * 10"
                )
                fields = panel.locator(".form-control")
                assert fields.count() >= 2
                for field in fields.all():
                    rectangle = field.bounding_box()
                    assert rectangle is not None
                    assert rectangle["x"] >= 0
                    assert rectangle["x"] + rectangle["width"] <= width
                    assert rectangle["width"] >= (
                        desktop_minimum if width >= 900 else 100
                    )
        finally:
            context.close()
            browser.close()
