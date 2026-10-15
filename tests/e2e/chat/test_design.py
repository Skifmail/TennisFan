"""Браузерные проверки удобства светлого адаптивного чата."""

import json
from os import environ
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен изолированный стенд"
)


def test_design_when_resized_or_keyboard_used_should_keep_composer_and_focus_accessible() -> (
    None
):
    """Панель ввода видна на телефоне, настройки управляются клавиатурой."""
    from playwright.sync_api import expect, sync_playwright

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
        )
        context.add_cookies(
            [
                {"name": cookie["name"], "value": cookie["value"], "url": base}
                for cookie in sessions["cookies"][0]
            ]
        )
        page = context.new_page()
        try:
            page.goto(base + sessions["room_path"])
            expect(page.locator("#chat-status")).to_have_text("На связи", timeout=15000)
            expect(
                page.get_by_role("navigation", name="Мои городские чаты")
            ).to_be_visible()
            expect(
                page.locator(".chat-room-link.is-selected .chat-participant-count")
            ).to_have_text("2 участника")
            for width, height in (
                (1440, 1000),
                (768, 1024),
                (390, 844),
                (320, 568),
                (390, 430),
            ):
                page.set_viewport_size({"width": width, "height": height})
                expect(page.locator("#chat-compose")).to_be_in_viewport(ratio=1)
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                assert page.evaluate(
                    "document.documentElement.scrollHeight <= innerHeight + 1"
                )
                page.locator("#chat-info").click()
                settings = page.get_by_role("dialog", name="О чате")
                expect(settings).to_be_visible()
                page.keyboard.press("Escape")
                expect(settings).not_to_be_visible()
                expect(page.locator("#chat-info")).to_be_focused()
            page.locator("#chat-text").fill("Длинное сообщение\n" * 30)
            expect(page.locator("#chat-compose")).to_be_in_viewport(ratio=1)
            assert page.locator("#chat-text").evaluate(
                "node => node.getBoundingClientRect().height <= 128"
            )
            page.locator("#chat-text").fill("Черновик сообщения")
            expect(page.locator("#chat-send")).to_be_enabled()
            page.reload()
            expect(page.locator("#chat-text")).to_have_value("Черновик сообщения")
            page.locator("#chat-text").fill("")
            expect(page.locator("#chat-send")).to_be_disabled()
            page.locator("#chat-text").fill("Проверка клавиатуры")
            page.locator("#chat-text").press("Shift+Enter")
            expect(page.locator("#chat-text")).to_have_value("Проверка клавиатуры\n")
            page.locator("#chat-text").press("Enter")
            expect(page.locator("#chat-text")).to_have_value("")
            own = page.locator(
                '.city-chat-message[data-own="true"][data-deleted="false"]'
            ).last
            options = own.get_by_role("button", name="Действия с сообщением")
            options.focus()
            options.press("Enter")
            expect(
                page.get_by_role("menuitem", name="Ответить", exact=True)
            ).to_be_focused()
            page.keyboard.press("ArrowDown")
            expect(
                page.get_by_role("menuitem", name="Изменить", exact=True)
            ).to_be_focused()
            page.keyboard.press("Escape")
            expect(options).to_be_focused()
            expect(page.locator("#chat-new")).not_to_be_visible()
            page.locator("#chat-text").fill("Несохранённый черновик")
            options.click()
            page.get_by_role("menuitem", name="Изменить", exact=True).click()
            page.locator("#chat-cancel").click()
            expect(page.locator("#chat-text")).to_have_value("Несохранённый черновик")
            page.goto(base + "/chats/?q=Казань")
            expect(page.get_by_role("heading", name="Найти свой город")).to_be_visible()
            joined = page.locator(".chat-city-card").filter(
                has=page.locator(f'a[href="{sessions["room_path"]}"]')
            )
            expect(joined.locator(".chat-participant-count")).to_have_text(
                "2 участника"
            )
            mobile_room = page.locator(
                f'.chat-mobile-memberships a[href="{sessions["room_path"]}"]'
            )
            expect(mobile_room.locator(".chat-participant-count")).to_have_text(
                "2 участника"
            )
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        finally:
            context.close()
            browser.close()
