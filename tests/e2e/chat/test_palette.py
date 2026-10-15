"""Проверка реальных цветов чата, диалогов и состояний в браузере."""

import json
from os import environ
from pathlib import Path
from time import sleep
from typing import Final

import pytest
from playwright.sync_api import Locator, expect, sync_playwright

from apps.chat.domain import MESSAGE_INTERVAL_SECONDS

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен изолированный стенд"
)
MINIMUM_TEXT_CONTRAST: Final = 4.5
CONTRAST_SCRIPT: Final = """node => {
    const RGB_MAX = 255;
    const LUMINANCE_OFFSET = 0.05;
    const weights = [0.2126, 0.7152, 0.0722];
    const parse = value => value.match(/[\\d.]+/g).map(Number);
    const composite = (front, back) => front.slice(0, 3).map(
        (channel, index) => channel * (front[3] ?? 1) + back[index] * (1 - (front[3] ?? 1))
    );
    const ancestors = [];
    for (let parent = node; parent; parent = parent.parentElement) ancestors.unshift(parent);
    let background = [RGB_MAX, RGB_MAX, RGB_MAX];
    for (const parent of ancestors) {
        background = composite(parse(getComputedStyle(parent).backgroundColor), background);
    }
    const foreground = composite(parse(getComputedStyle(node).color), background);
    const luminance = color => color.reduce((sum, channel, index) => {
        const value = channel / RGB_MAX;
        const linear = value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
        return sum + linear * weights[index];
    }, 0);
    const values = [luminance(foreground), luminance(background)].sort((a, b) => a - b);
    return (values[1] + LUMINANCE_OFFSET) / (values[0] + LUMINANCE_OFFSET);
}"""


def _assert_readable(locator: Locator) -> None:
    expect(locator).to_be_visible()
    assert float(locator.evaluate(CONTRAST_SCRIPT)) >= MINIMUM_TEXT_CONTRAST


def test_palette_when_chat_states_rendered_should_keep_brand_and_text_readable() -> (
    None
):
    """Зелёные действия и текст читаемы в каталоге, переписке и настройках."""
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
            page.goto(base + "/chats/?q=Казань")
            _assert_readable(page.locator(".chat-eyebrow"))
            _assert_readable(page.locator(".chat-city-search button"))
            _assert_readable(page.locator(".chat-join-button").first)
            page.goto(base + sessions["room_path"])
            expect(page.locator("#chat-status")).to_have_text("На связи", timeout=15000)
            _assert_readable(page.locator(".chat-room-link.is-selected small"))
            page.locator("#chat-text").fill("Проверка фирменной палитры")
            expect(page.locator("#chat-send")).to_be_enabled()
            _assert_readable(page.locator("#chat-send"))
            # Сессия общая с соседними E2E: соблюдаем серверный антиспам.
            sleep(MESSAGE_INTERVAL_SECONDS)
            page.locator("#chat-text").press("Enter")
            expect(page.locator("#chat-text")).to_have_value("")
            own = page.locator(
                '.city-chat-message[data-own="true"][data-deleted="false"]'
            ).last
            expect(own.locator(".city-chat-message-text")).to_have_text(
                "Проверка фирменной палитры"
            )
            for contrast in ("no-preference", "more"):
                page.emulate_media(contrast=contrast)
                _assert_readable(own.locator(".chat-bubble"))
                _assert_readable(own.locator(".chat-message-meta"))
                _assert_readable(page.locator("#chat-send"))
                assert own.locator(".chat-bubble").evaluate(
                    "node => { const [r,g,b] = getComputedStyle(node).backgroundColor"
                    ".match(/\\d+/g).map(Number); return g > r && g > b; }"
                )
            page.emulate_media(contrast="no-preference")
            page.locator("#chat-info").click()
            dialog = page.get_by_role("dialog", name="О чате")
            _assert_readable(dialog.locator(".chat-settings-identity p"))
            for button in dialog.locator(".chat-leave-button").all():
                _assert_readable(button)
            page.keyboard.press("Escape")
            options = own.get_by_role("button", name="Действия с сообщением")
            options.focus()
            options.press("Enter")
            destructive = page.get_by_role("menuitem", name="Удалить", exact=True)
            _assert_readable(destructive)
            destructive.hover()
            _assert_readable(destructive)
            destructive.click()
            confirm = page.locator("#chat-action-confirm")
            _assert_readable(confirm)
            background = confirm.evaluate(
                "node => getComputedStyle(node).backgroundColor"
            )
            confirm.hover()
            expect(confirm).to_have_css("background-color", background)
            _assert_readable(confirm)
            _assert_readable(page.locator("#chat-action-dialog .chat-button-secondary"))
            page.keyboard.press("Escape")
        finally:
            context.close()
            browser.close()
