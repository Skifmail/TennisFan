"""Браузерная проверка отдельного чата владельца платформы."""

import json
from os import environ
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен локальный браузерный стенд"
)


def test_custom_room_when_owner_creates_should_allow_player_to_join() -> None:
    """Создание, личные согласия, поиск и переписка работают без города."""
    from playwright.sync_api import expect, sync_playwright

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    assert base == "http://127.0.0.1:18765"
    title = f"Общий теннис {uuid4().hex[:8]}"
    artifacts = Path(environ.get("CHAT_E2E_ARTIFACTS", "/tmp/chat-custom-browser"))
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        owner_context = browser.new_context(viewport={"width": 390, "height": 1000})
        player_context = browser.new_context(viewport={"width": 1440, "height": 1000})
        for context, cookies in (
            (owner_context, sessions["admin_cookies"]),
            (player_context, sessions["cookies"][0]),
        ):
            context.add_cookies(
                [
                    {"name": item["name"], "value": item["value"], "url": base}
                    for item in cookies
                ]
            )
        owner, player = owner_context.new_page(), player_context.new_page()
        try:
            owner.goto(base + "/chats/")
            owner.locator("main").get_by_role(
                "link", name="Создать отдельный чат", exact=True
            ).click()
            owner.get_by_label("Название чата", exact=True).fill(title)
            owner.get_by_role("button", name="Создать чат", exact=True).click()
            expect(
                owner.get_by_role("heading", name="Вступление в чат", exact=True)
            ).to_be_visible()
            assert not owner.locator('input[name="personal_data"]').is_checked()
            owner.locator('input[name="personal_data"]').check()
            owner.locator('input[name="rules"]').check()
            owner.get_by_role(
                "button", name="Подтвердить и вступить", exact=True
            ).click()
            owner.wait_for_url("**/chats/*/")
            expect(owner.get_by_role("heading", name=title, exact=True)).to_be_visible()
            owner.screenshot(path=str(artifacts / "custom-room-mobile.png"))
            player.goto(base + "/chats/")
            player.locator("#chat-city-search").fill(title)
            player.get_by_role("button", name="Найти", exact=True).click()
            player.locator(".chat-city-card").filter(has_text=title).get_by_role(
                "link", name="Вступить"
            ).click()
            player.locator('input[name="personal_data"]').check()
            player.locator('input[name="rules"]').check()
            player.get_by_role(
                "button", name="Подтвердить и вступить", exact=True
            ).click()
            player.wait_for_url("**/chats/*/")
            expect(
                player.get_by_role("heading", name=title, exact=True)
            ).to_be_visible()
            player.screenshot(path=str(artifacts / "custom-room-desktop.png"))
            assert player.locator(".chat-app-frame").evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
            owner.get_by_role("button", name="Настройки чата", exact=True).click()
            owner.get_by_role("link", name="Пригласить участников", exact=True).click()
            expect(
                owner.get_by_role("heading", name="Пригласить участников", exact=True)
            ).to_be_visible()
            assert owner.locator('input[name="audience"][value="city"]').count() == 0
        finally:
            owner_context.close()
            player_context.close()
            browser.close()
