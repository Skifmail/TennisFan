"""Браузерное подтверждение трёх документов без заранее отмеченных галочек."""

import json
from os import environ
from pathlib import Path
from time import sleep
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен изолированный стенд"
)


def test_join_when_modal_opened_should_reset_checkboxes_and_support_mobile_keyboard() -> (
    None
):
    """Модальное окно доступно с клавиатуры и не запоминает отменённые согласия."""
    from playwright.sync_api import expect, sync_playwright

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(
            viewport={"width": 390, "height": 844}, reduced_motion="reduce"
        )
        context.add_cookies(
            [
                {"name": cookie["name"], "value": cookie["value"], "url": base}
                for cookie in sessions["cookies"][0]
            ]
        )
        page = context.new_page()
        try:
            page.goto(base + "/chats/?q=" + sessions["consent_city_name"])
            trigger = page.locator("[data-chat-join]").first
            expect(trigger).to_be_visible()
            trigger.click()
            dialog = page.get_by_role("dialog", name="Вступление в городской чат")
            expect(dialog).to_be_visible()
            consents = dialog.locator('input[type="checkbox"][required]')
            expect(consents).to_have_count(3)
            for checkbox in consents.all():
                expect(checkbox).not_to_be_checked()
            expect(
                dialog.get_by_role("button", name="Подтвердить и вступить")
            ).to_be_disabled()
            consents.first.check()
            page.keyboard.press("Escape")
            expect(dialog).not_to_be_visible()
            expect(trigger).to_be_focused()
            trigger.press("Enter")
            expect(dialog).to_be_visible()
            for checkbox in consents.all():
                expect(checkbox).not_to_be_checked()
                checkbox.check()
            expect(
                dialog.get_by_role("button", name="Подтвердить и вступить")
            ).to_be_enabled()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.keyboard.press("Escape")
        finally:
            context.close()
            browser.close()


def test_consent_when_joined_and_withdrawn_should_redact_live_messages_for_other_player() -> (
    None
):
    """Реальный POST вступает в чат, отзыв обновляет уже открытый второй браузер."""
    from playwright.sync_api import expect, sync_playwright

    from apps.chat.domain import MESSAGE_INTERVAL_SECONDS

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        contexts = [
            browser.new_context(viewport={"width": 1280, "height": 900})
            for _ in range(2)
        ]
        try:
            pages = []
            for index, context in enumerate(contexts):
                context.add_cookies(
                    [
                        {"name": cookie["name"], "value": cookie["value"], "url": base}
                        for cookie in sessions["cookies"][index]
                    ]
                )
                page = context.new_page()
                page.goto(base + f'/chats/join/?city_id={sessions["consent_city_id"]}')
                for checkbox in page.locator(
                    '[data-consent-form] input[type="checkbox"][required]'
                ).all():
                    checkbox.check()
                page.get_by_role("button", name="Подтвердить и вступить").click()
                expect(page.locator("#chat-status")).to_have_text(
                    "На связи", timeout=15000
                )
                pages.append(page)
            first, second = pages
            text = "Проверка отзыва " + str(uuid4())
            sleep(MESSAGE_INTERVAL_SECONDS)
            first.locator("#chat-text").fill(text)
            first.locator("#chat-send").click()
            incoming = second.locator(".city-chat-message").filter(has_text=text)
            expect(incoming).to_have_count(1)
            expect(incoming.locator(".chat-message-author")).to_contain_text(
                "Участник #"
            )
            message_id = incoming.get_attribute("data-message-id")
            assert message_id is not None
            first.locator("#chat-info").click()
            first.get_by_role("button", name="Отозвать согласия на этот чат").click()
            expect(
                first.get_by_role("heading", name="Найти свой город")
            ).to_be_visible()
            expect(
                second.locator(f'.city-chat-message[data-message-id="{message_id}"]')
            ).to_have_attribute("data-deleted", "true", timeout=15000)
            expect(
                second.locator(
                    f'.city-chat-message[data-message-id="{message_id}"] .city-chat-message-text'
                )
            ).not_to_have_text(text)
        finally:
            for context in contexts:
                context.close()
            browser.close()


def test_consent_when_javascript_disabled_should_render_unchecked_server_form() -> None:
    """Недоступность модального интерфейса не лишает игрока документов и выбора."""
    from playwright.sync_api import expect, sync_playwright

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(
            java_script_enabled=False, viewport={"width": 390, "height": 844}
        )
        try:
            context.add_cookies(
                [
                    {"name": cookie["name"], "value": cookie["value"], "url": base}
                    for cookie in sessions["cookies"][0]
                ]
            )
            page = context.new_page()
            page.goto(base + f'/chats/join/?city_id={sessions["consent_city_id"]}')
            fields = page.locator(
                '[data-consent-form] input[type="checkbox"][required]'
            )
            expect(fields).to_have_count(3)
            for field in fields.all():
                expect(field).not_to_be_checked()
            expect(
                page.get_by_role("link", name="Прочитать правила чатов ↗")
            ).to_be_visible()
            expect(
                page.get_by_role("button", name="Подтвердить и вступить")
            ).to_be_enabled()
        finally:
            context.close()
            browser.close()
