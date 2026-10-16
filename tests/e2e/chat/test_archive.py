"""Полный браузерный путь от удаления сообщения до отдельного удаления архива."""

import json
from os import environ
from pathlib import Path
from time import sleep

import pytest

from apps.chat.domain import MESSAGE_INTERVAL_SECONDS

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен изолированный браузерный стенд"
)


def test_archive_when_message_and_room_deleted_should_remain_until_owner_purges() -> (
    None
):
    """Игрок скрывает сообщение, владелец видит редакции и отдельно удаляет архив."""
    from playwright.sync_api import expect, sync_playwright

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        player_context = browser.new_context(viewport={"width": 1440, "height": 1000})
        owner_context = browser.new_context(viewport={"width": 1440, "height": 1000})
        for context, cookies in (
            (player_context, sessions["cookies"][0]),
            (owner_context, sessions["admin_cookies"]),
        ):
            context.add_cookies(
                [
                    {"name": cookie["name"], "value": cookie["value"], "url": base}
                    for cookie in cookies
                ]
            )
        player = player_context.new_page()
        owner = owner_context.new_page()
        try:
            player.goto(base + f'/chats/join/?city_id={sessions["archive_city_id"]}')
            for name in ("personal_data", "rules"):
                player.locator(f'input[name="{name}"]').check()
            player.get_by_role("button", name="Подтвердить и создать чат").click()
            player.wait_for_url("**/chats/*/")
            room_id = player.url.rstrip("/").rsplit("/", 1)[-1]
            expect(player.locator("#chat-status")).to_have_text("На связи")
            sleep(MESSAGE_INTERVAL_SECONDS + 1)
            player.locator("#chat-text").fill("Исходный текст <архив>")
            player.locator("#chat-text").press("Enter")
            message = player.locator(
                '.city-chat-message[data-own="true"][data-deleted="false"]'
            ).last
            message.get_by_role("button", name="Действия с сообщением").click()
            player.get_by_role("menuitem", name="Изменить", exact=True).click()
            player.locator("#chat-text").fill("Исправленный текст архива")
            player.locator("#chat-send").click()
            expect(message.locator(".chat-bubble")).to_contain_text(
                "Исправленный текст архива"
            )
            message.get_by_role("button", name="Действия с сообщением").click()
            player.get_by_role("menuitem", name="Удалить", exact=True).click()
            player.locator("#chat-action-confirm").click()
            expect(
                player.locator('.city-chat-message[data-deleted="true"]').last
            ).to_contain_text("Сообщение удалено")
            owner.goto(base + f"/admin/chat/chatroom/{room_id}/change/")
            expect(
                owner.get_by_text("Исходный текст <архив>", exact=True)
            ).to_have_count(0)
            owner.get_by_role("link", name="Открыть архив переписки").click()
            archive_url = owner.url
            owner.locator('input[name="q"]').fill("Исходный текст")
            owner.get_by_role("button", name="Найти", exact=True).click()
            expect(owner.locator("#chat-archive-messages tbody tr")).to_have_count(1)
            owner.get_by_role("link", name="Все версии", exact=True).click()
            expect(
                owner.get_by_text("Исходный текст <архив>", exact=True)
            ).to_be_visible()
            expect(
                owner.get_by_text("Исправленный текст архива", exact=True)
            ).to_have_count(2)
            owner.goto(base + f"/admin/chat/chatroom/{room_id}/delete/")
            expect(
                owner.get_by_text("Закрытый архив переписки", exact=False)
            ).to_be_visible()
            owner.locator('input[type="submit"]').click()
            owner.goto(archive_url)
            expect(owner.locator("#chat-archive-messages tbody tr")).to_have_count(1)
            expect(owner.get_by_text("Удалено из чата", exact=True)).to_be_visible()
            artifacts = Path(
                environ.get("CHAT_E2E_ARTIFACTS", "/tmp/chat-archive-browser")
            )
            artifacts.mkdir(parents=True, exist_ok=True)
            owner.screenshot(path=str(artifacts / "archive-after-room-deletion.png"))
            owner.get_by_role("link", name="Все версии", exact=True).click()
            owner.screenshot(path=str(artifacts / "archive-message-versions.png"))
            owner.goto(archive_url)
            owner.get_by_role(
                "link", name="Удалить архив безвозвратно", exact=True
            ).click()
            expect(
                owner.get_by_text(
                    "Вы удаляете архив переписки безвозвратно", exact=False
                )
            ).to_be_visible()
            owner.locator('input[type="submit"]').click()
            expect(owner).to_have_url(base + "/admin/chat/chatarchive/")
            expect(
                owner.locator(f'a[href="{archive_url.removeprefix(base)}"]')
            ).to_have_count(0)
            response = owner.request.get(archive_url)
            assert response.status in {200, 302, 404}
            assert "Исходный текст &lt;архив&gt;" not in response.text()
        finally:
            player_context.close()
            owner_context.close()
            browser.close()
