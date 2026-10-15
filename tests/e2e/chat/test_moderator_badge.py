"""Браузерная проверка бейджа, подписи и смены роли без новых сообщений."""

import json
from os import environ
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Route, expect, sync_playwright

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен изолированный стенд"
)


def test_badge_when_role_updated_should_render_own_and_other_names_accessibly() -> None:
    """Бейдж справа, подпись снизу; снятие роли обновляет уже показанные сообщения."""
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
            config = json.loads(page.locator("#chat-config").inner_text())
            own_id = config["userId"]
            other_id = own_id + 1
            moderators = [own_id, other_id]
            messages: list[dict[str, Any]] = [
                {
                    "id": index,
                    "revision": index,
                    "author_id": author_id,
                    "author": name,
                    "author_is_moderator": author_id in moderators,
                    "avatar": "",
                    "profile_url": "",
                    "text": "До встречи на корте!",
                    "client_id": str(index),
                    "created_at": "2026-10-15T12:00:00Z",
                    "is_edited": False,
                    "is_deleted": False,
                    "reply_to": None,
                }
                for index, (author_id, name) in enumerate(
                    (
                        (own_id, "Анна Александровна Константинопольская"),
                        (other_id, "Михаил Александрович Константинопольский"),
                        (other_id + 1, "Обычный участник <img src=x>"),
                    ),
                    start=1,
                )
            ]

            def _history(route: Route) -> None:
                route.fulfill(
                    json={"messages": messages, "cursor": 0, "has_more": False}
                )

            def _sync(route: Route) -> None:
                route.fulfill(
                    json={
                        "events": [],
                        "cursor": 0,
                        "has_more": False,
                        "moderator_ids": moderators,
                        "can_moderate": False,
                        "banned_until": None,
                        "rules": "Общайтесь уважительно",
                    }
                )

            # Проверяем UI независимо от прав реальных тестовых аккаунтов.
            page.route("**/chats/api/rooms/*/history/**", _history)
            page.route("**/chats/api/rooms/*/sync/**", _sync)
            page.route(
                "**/chats/api/rooms/*/action/", lambda route: route.fulfill(json={})
            )
            page.reload()
            badges = page.locator(".chat-moderator-badge")
            expect(badges).to_have_count(2)
            normal = page.locator('.city-chat-message[data-message-id="3"]')
            expect(normal.locator(".chat-moderator-label")).to_have_count(0)
            expect(normal.locator(".chat-message-author img")).to_have_count(0)
            for width in (1440, 390, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                for message_id in (1, 2):
                    message = page.locator(
                        f'.city-chat-message[data-message-id="{message_id}"]'
                    )
                    badge = message.locator(".chat-moderator-badge")
                    label = message.locator(".chat-moderator-label")
                    expect(label).to_have_text("Модератор")
                    expect(badge).to_be_visible()
                    expect(badge).to_have_js_property("complete", True)
                    expect(badge).to_have_attribute("alt", "")
                    assert badge.evaluate(
                        "node => node.complete && node.naturalWidth > 0"
                    )
                    name_box = message.locator(".chat-message-author").bounding_box()
                    badge_box = badge.bounding_box()
                    label_box = label.bounding_box()
                    column_box = message.locator(".chat-message-column").bounding_box()
                    assert name_box and badge_box and label_box and column_box
                    assert badge_box["x"] >= name_box["x"] + name_box["width"]
                    assert label_box["y"] >= name_box["y"] + name_box["height"]
                    assert badge_box["x"] >= column_box["x"]
                    assert badge_box["x"] + badge_box["width"] <= (
                        column_box["x"] + column_box["width"]
                    )
            moderators.clear()
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            expect(badges).to_have_count(0)
            expect(page.locator(".chat-moderator-label")).to_have_count(0)
            expect(page.locator('.city-chat-message[data-own="true"]')).to_have_count(1)
        finally:
            context.close()
            browser.close()
