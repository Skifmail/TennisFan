"""Создание и передача городского сообщества в настоящем браузере."""

import json
from os import environ
from pathlib import Path
from time import sleep

import pytest

from apps.chat.domain import MESSAGE_INTERVAL_SECONDS

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен изолированный стенд"
)


def test_governance_when_city_created_and_transferred_should_update_permissions() -> (
    None
):
    """Игрок создаёт чат, назначает помощника и передаёт управление без прав платформы."""
    from playwright.sync_api import expect, sync_playwright

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        contexts = [
            browser.new_context(
                viewport={"width": 390, "height": 844}, reduced_motion="reduce"
            )
            for _ in range(2)
        ]
        for index, context in enumerate(contexts):
            context.add_cookies(
                [
                    {"name": cookie["name"], "value": cookie["value"], "url": base}
                    for cookie in sessions["cookies"][index]
                ]
            )
        creator, participant = (context.new_page() for context in contexts)
        try:
            creator.goto(base + "/chats/?q=" + sessions["creation_city_name"])
            creator.get_by_role("link", name="Создать чат", exact=True).click()
            dialog = creator.get_by_role("dialog", name="Создание городского чата")
            expect(
                dialog.get_by_text("Вы станете администратором", exact=False)
            ).to_be_visible()
            for name in ("personal_data", "rules"):
                dialog.locator(f'input[name="{name}"]').check()
            dialog.get_by_role("button", name="Подтвердить и создать чат").click()
            creator.wait_for_url("**/chats/*/")
            room_url = creator.url
            expect(creator.locator("#chat-status")).to_have_text("На связи")
            # Те же игроки используются другими сценариями: соблюдаем общий антиспам.
            sleep(MESSAGE_INTERVAL_SECONDS + 1)
            creator.locator("#chat-text").fill("Добро пожаловать в наше сообщество!")
            creator.locator("#chat-text").press("Enter")
            owner_badge = creator.locator(
                '.city-chat-message[data-own="true"] .chat-moderator-badge'
            ).last
            expect(owner_badge).to_have_attribute(
                "src", "/static/images/chat/administrator-badge.webp"
            )
            expect(
                creator.locator(
                    '.city-chat-message[data-own="true"] .chat-moderator-label'
                ).last
            ).to_have_text("Администратор")
            expect(owner_badge).to_have_js_property("naturalWidth", 56)
            participant.goto(
                base + f'/chats/join/?city_id={sessions["creation_city_id"]}'
            )
            for name in ("personal_data", "rules"):
                participant.locator(f'input[name="{name}"]').check()
            participant.get_by_role("button", name="Подтвердить и вступить").click()
            participant.wait_for_url(room_url)
            participant.locator("#chat-text").fill("Помогу следить за порядком")
            participant.locator("#chat-text").press("Enter")
            creator.locator("#chat-info").click()
            creator.get_by_role("link", name="Управление участниками").click()
            creator.get_by_role(
                "button", name="Назначить модератором", exact=True
            ).click()
            expect(
                creator.get_by_role("button", name="Снять модератора", exact=True)
            ).to_be_visible()
            expect(
                participant.locator(
                    '.city-chat-message[data-own="true"] .chat-moderator-label'
                ).last
            ).to_have_text("Модератор")
            expect(
                participant.locator(
                    '.city-chat-message[data-own="true"] .chat-moderator-badge'
                ).last
            ).to_have_attribute("src", "/static/images/chat/moderator-badge.png")
            assert creator.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            )
            artifacts = Path(
                environ.get("CHAT_E2E_ARTIFACTS", "/tmp/chat-governance-browser")
            )
            artifacts.mkdir(parents=True, exist_ok=True)
            creator.screenshot(path=str(artifacts / "management-mobile.png"))
            creator.locator('[data-transfer-form] input[type="checkbox"]').check()
            creator.get_by_role(
                "button", name="Передать управление", exact=True
            ).click()
            creator.wait_for_url(room_url)
            creator.locator("#chat-info").click()
            expect(
                creator.get_by_role("link", name="Управление участниками")
            ).not_to_be_visible()
            participant.locator("#chat-info").click()
            expect(
                participant.get_by_role("link", name="Управление участниками")
            ).to_be_visible()
            participant.keyboard.press("Escape")
            expect(
                participant.locator(
                    '.city-chat-message[data-own="true"] .chat-moderator-label'
                ).last
            ).to_have_text("Администратор")
            new_badge = participant.locator(
                '.city-chat-message[data-own="true"] .chat-moderator-badge'
            ).last
            expect(new_badge).to_have_attribute(
                "src", "/static/images/chat/administrator-badge.webp"
            )
            expect(new_badge).to_have_js_property("naturalWidth", 56)
            participant.screenshot(
                path=str(artifacts / "administrator-badge-mobile.png")
            )
            admin_context = browser.new_context()
            try:
                admin_context.add_cookies(
                    [
                        {"name": cookie["name"], "value": cookie["value"], "url": base}
                        for cookie in sessions["admin_cookies"]
                    ]
                )
                admin_page = admin_context.new_page()
                room_id = room_url.rstrip("/").rsplit("/", 1)[-1]
                admin_page.goto(
                    base + "/admin/chat/chatroom/?q=" + sessions["creation_city_name"]
                )
                admin_page.locator(f'input.action-select[value="{room_id}"]').check()
                admin_page.locator('select[name="action"]').select_option(
                    "delete_selected"
                )
                admin_page.locator('button[name="index"]').first.click()
                expect(admin_page.locator('input[name="post"]')).to_have_value("yes")
                admin_page.locator('input[type="submit"]').click()
                expect(
                    admin_page.locator(f'input.action-select[value="{room_id}"]')
                ).to_have_count(0)
            finally:
                admin_context.close()
        finally:
            for context in contexts:
                context.close()
            browser.close()
