"""Личный разговор из карточки и профиля с проверкой реальной доставки в браузере."""

import json
from os import environ
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен локальный стенд"
)


def test_direct_when_started_from_sparring_should_deliver_and_reopen_from_profile() -> (
    None
):
    """Два браузера обмениваются сообщениями; посторонний не подключается к WebSocket."""
    from playwright.sync_api import expect, sync_playwright

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    assert base == "http://127.0.0.1:18765"
    artifacts = Path(environ.get("CHAT_E2E_ARTIFACTS", "/tmp/tennison-direct-browser"))
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        contexts = [
            browser.new_context(viewport={"width": width, "height": 1000})
            for width in (1440, 390, 390)
        ]
        for context, cookies in zip(
            contexts, [*sessions["cookies"], sessions["admin_cookies"]], strict=True
        ):
            context.add_cookies(
                [
                    {"name": cookie["name"], "value": cookie["value"], "url": base}
                    for cookie in cookies
                ]
            )
        sender, recipient, outsider = [context.new_page() for context in contexts]
        try:
            sender.goto(base + "/sparring/?type=singles")
            card = sender.locator(f'#sparring-singles-{sessions["direct_sparring_id"]}')
            expect(card).to_be_visible()
            card.get_by_role("button", name="Написать", exact=False).click()
            sender.locator('input[name="personal_data"]').check()
            sender.locator('input[name="rules"]').check()
            sender.get_by_role(
                "button", name="Подтвердить и вступить", exact=True
            ).click()
            sender.wait_for_url("**/chats/*/")
            room_url = sender.url
            expect(sender.locator("#chat-text")).to_be_enabled()
            sender.locator("#chat-text").fill("Личное приглашение сыграть")
            sender.get_by_role("button", name="Отправить", exact=True).click()
            expect(sender.locator("#chat-messages")).to_contain_text(
                "Личное приглашение сыграть"
            )
            recipient.goto(base + "/users/notifications/")
            recipient.locator(f'a[href="{room_url.removeprefix(base)}"]').first.click()
            recipient.locator('input[name="personal_data"]').check()
            recipient.locator('input[name="rules"]').check()
            recipient.get_by_role(
                "button", name="Подтвердить и вступить", exact=True
            ).click()
            expect(recipient.locator("#chat-messages")).to_contain_text(
                "Личное приглашение сыграть"
            )
            recipient.locator("#chat-text").fill("Договорились!")
            recipient.get_by_role("button", name="Отправить", exact=True).click()
            expect(sender.locator("#chat-messages")).to_contain_text("Договорились!")
            sender.goto(base + f'/users/profile/{sessions["direct_target_player_id"]}/')
            sender.get_by_role("button", name="Написать", exact=False).click()
            sender.wait_for_url(room_url)
            expect(
                sender.get_by_role("navigation", name="Личные чаты")
            ).to_contain_text("Михаил")
            sender.screenshot(path=str(artifacts / "direct-desktop.png"))
            recipient.screenshot(path=str(artifacts / "direct-mobile.png"))
            assert recipient.locator(".chat-app-frame").evaluate(
                "e => e.scrollWidth <= e.clientWidth"
            )
            outsider.goto(base + "/chats/")
            code = outsider.evaluate(
                """path => new Promise(resolve => {const socket = new WebSocket(location.origin.replace('http', 'ws') + '/ws' + path); socket.onopen = () => resolve('opened'); socket.onclose = e => resolve(e.code); socket.onerror = () => {};})""",
                room_url.removeprefix(base),
            )
            assert code != "opened"
        finally:
            for context in contexts:
                context.close()
            browser.close()
