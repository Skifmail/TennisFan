"""Браузерная проверка управления участниками внутри выбранного городского чата."""

import json
from os import environ
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен изолированный браузерный стенд"
)


def test_admin_when_member_managed_should_stay_inside_selected_room() -> None:
    """Добавление, редактирование и удаление выполняются из карточки города."""
    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        context.add_cookies(
            [
                {"name": cookie["name"], "value": cookie["value"], "url": base}
                for cookie in sessions["admin_cookies"]
            ]
        )
        page = context.new_page()
        try:
            page.goto(base + sessions["admin_room_path"])
            expect(page.locator("#chat-room-messages")).to_be_visible()
            expect(page.locator("#chat-room-members")).to_be_visible()
            expect(
                page.locator('#nav-sidebar a[href*="/chat/chatmembership/"]')
            ).to_have_count(0)
            expect(
                page.locator('#nav-sidebar a[href*="/chat/chatmessage/"]')
            ).to_have_count(0)
            page.get_by_role("link", name="Добавить участника", exact=True).click()
            expect(page.locator('#nav-sidebar a[aria-current="page"]')).to_have_text(
                "Городские чаты"
            )
            expect(page.locator("#id_room")).to_be_disabled()
            page.locator(".select2-selection").click()
            page.locator(".select2-search__field").fill(sessions["admin_email"])
            page.get_by_role("option", name=sessions["admin_email"], exact=True).click()
            page.locator("#id_is_moderator").check()
            page.locator('[name="_save"]').click()
            expect(page).to_have_url(
                base + sessions["admin_room_path"] + "#chat-room-members"
            )
            row = page.locator("#chat-room-members tbody tr").filter(
                has_text=sessions["admin_email"]
            )
            expect(row).to_have_count(1)
            expect(row.locator("td").nth(2)).to_have_text("Да")
            row.get_by_role("link", name="Редактировать", exact=True).click()
            expect(page.locator("#id_is_moderator")).to_be_checked()
            page.locator("#id_is_moderator").uncheck()
            page.locator("#id_notifications_enabled").uncheck()
            page.locator('[name="_save"]').click()
            expect(row.locator("td").nth(2)).to_have_text("Нет")
            expect(row.locator("td").nth(4)).to_have_text("Нет")
            row.get_by_role("link", name="Удалить", exact=True).click()
            expect(
                page.get_by_text(
                    "Аккаунт пользователя и его сообщения сохранятся.", exact=False
                )
            ).to_be_visible()
            page.locator('input[type="submit"]').click()
            expect(page).to_have_url(
                base + sessions["admin_room_path"] + "#chat-room-members"
            )
            expect(row).to_have_count(0)
            expect(page.locator("#chat-room-members tbody tr")).to_have_count(2)
        finally:
            context.close()
            browser.close()
