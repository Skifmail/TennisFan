"""Браузерное подтверждение двух обязательных и добровольного согласия без заранее отмеченных галочек."""

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
            expect(
                dialog.get_by_text("Выберите, что показывать", exact=True)
            ).not_to_be_visible()
            for field_name in ("show_name", "show_avatar"):
                expect(
                    dialog.locator(f'input[name="{field_name}"]')
                ).not_to_be_checked()
            expect(dialog.locator('textarea[name="conditions"]')).to_have_value("")
            consents = dialog.locator('input[type="checkbox"][required]')
            expect(consents).to_have_count(2)
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
            disclosure = dialog.locator('input[name="disclosure"]')
            name = dialog.locator('input[name="show_name"]')
            photo = dialog.locator('input[name="show_avatar"]')
            submit = dialog.get_by_role("button", name="Подтвердить и вступить")
            disclosure.check()
            expect(name).to_be_visible()
            expect(photo).to_be_visible()
            expect(submit).to_be_disabled()
            name.check()
            expect(submit).to_be_enabled()
            name.uncheck()
            expect(submit).to_be_disabled()
            photo.check()
            expect(submit).to_be_enabled()
            photo.uncheck()
            expect(submit).to_be_disabled()
            name.check()
            photo.check()
            dialog.locator(".chat-consent-conditions summary").click()
            conditions = dialog.locator('textarea[name="conditions"]')
            conditions.fill("Проверка сброса условий")
            disclosure.uncheck()
            expect(name).not_to_be_visible()
            expect(photo).not_to_be_visible()
            expect(name).not_to_be_checked()
            expect(photo).not_to_be_checked()
            expect(conditions).to_have_value("")
            expect(submit).to_be_enabled()
            disclosure.check()
            expect(submit).to_be_disabled()
            disclosure.uncheck()
            consents.first.uncheck()
            expect(submit).to_be_disabled()
            consents.first.check()
            expect(submit).to_be_enabled()
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
            first.get_by_role(
                "button", name="Отозвать основные согласия и выйти"
            ).click()
            confirm = first.get_by_role("dialog", name="Отозвать основные согласия?")
            expect(confirm).to_be_visible()
            confirm.get_by_role("button", name="Отозвать и выйти", exact=True).click()
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
            expect(fields).to_have_count(2)
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


def test_settings_when_profile_choices_changed_and_withdrawn_should_apply_and_explain() -> (
    None
):
    """Настройки слева меняют все комбинации данных; отзыв явно завершает участие."""
    from playwright.sync_api import expect, sync_playwright

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_cookies(
            [
                {"name": cookie["name"], "value": cookie["value"], "url": base}
                for cookie in sessions["cookies"][0]
            ]
        )
        page = context.new_page()
        try:
            page.goto(base + f'/chats/join/?city_id={sessions["consent_city_id"]}')
            for checkbox in page.locator(
                '[data-consent-form] input[required][type="checkbox"]'
            ).all():
                checkbox.check()
            page.locator('[data-consent-form] button[type="submit"]').click()
            expect(page.locator("#chat-sidebar-settings")).to_be_visible()
            room_path = page.url
            for name_enabled, photo_enabled in (
                (True, False),
                (False, True),
                (True, True),
                (False, False),
            ):
                page.locator("#chat-sidebar-settings").click()
                settings = page.get_by_role("dialog", name="Настройки чата")
                expect(settings).to_be_visible()
                settings.get_by_role(
                    "link", name="Изменить согласия", exact=True
                ).click()
                expect(
                    page.get_by_role("heading", name="Изменить согласия", exact=True)
                ).to_be_visible()
                for checkbox in page.locator(
                    '[data-consent-form] input[required][type="checkbox"]'
                ).all():
                    expect(checkbox).to_be_checked()
                if name_enabled or photo_enabled:
                    page.locator('input[name="disclosure"]').check()
                    page.locator('input[name="show_name"]').set_checked(name_enabled)
                    page.locator('input[name="show_avatar"]').set_checked(photo_enabled)
                else:
                    page.locator('input[name="disclosure"]').uncheck()
                page.get_by_role(
                    "button", name="Сохранить изменения", exact=True
                ).click()
                expect(page).to_have_url(room_path)
                expect(
                    page.get_by_role("status").filter(has_text="Согласия обновлены")
                ).to_be_visible()
                page.locator("#chat-sidebar-settings").click()
                expect(page.locator('[data-profile-visibility="name"]')).to_have_text(
                    "Показываются" if name_enabled else "Скрыты"
                )
                expect(page.locator('[data-profile-visibility="avatar"]')).to_have_text(
                    "Не загружено" if photo_enabled else "Скрыто"
                )
                expect(
                    page.get_by_role("link", name="Добавить фото в профиль", exact=True)
                ).to_be_visible()
                page.keyboard.press("Escape")
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator("#chat-info").click()
            page.get_by_role(
                "button", name="Отозвать основные согласия и выйти", exact=True
            ).click()
            confirm = page.get_by_role("dialog", name="Отозвать основные согласия?")
            confirm.get_by_role("button", name="Отмена", exact=True).click()
            expect(page).to_have_url(room_path)
            page.locator("#chat-info").click()
            page.get_by_role(
                "button", name="Отозвать основные согласия и выйти", exact=True
            ).click()
            page.get_by_role("button", name="Отозвать и выйти", exact=True).click()
            expect(
                page.get_by_role("status").filter(has_text="исключены из чата")
            ).to_be_visible()
            expect(page).to_have_url(base + "/chats/")
            page.goto(room_path)
            expect(page).to_have_url(base + "/chats/")
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize("field", ["personal_data", "rules"])
def test_settings_when_main_checkbox_unchecked_should_confirm_restore_or_exclude(
    field: str,
) -> None:
    """Снятие любой основной отметки требует подтверждения; отмена сохраняет участие.

    Args:
        field: Основное подтверждение, отзываемое пользователем.
    """
    from playwright.sync_api import expect, sync_playwright

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies(
            [
                {"name": cookie["name"], "value": cookie["value"], "url": base}
                for cookie in sessions["cookies"][0]
            ]
        )
        page = context.new_page()
        try:
            page.goto(base + f'/chats/join/?city_id={sessions["consent_city_id"]}')
            for checkbox in page.locator(
                '[data-consent-form] input[required][type="checkbox"]'
            ).all():
                checkbox.check()
            page.locator('[data-consent-form] button[type="submit"]').click()
            expect(page.locator("#chat-status")).to_have_text("На связи", timeout=15000)
            page.locator("#chat-info").click()
            page.get_by_role("link", name="Изменить согласия", exact=True).click()
            edit_url = page.url
            main = page.locator(f'input[name="{field}"]')
            expect(main).to_be_checked()
            other_field = "rules" if field == "personal_data" else "personal_data"
            expect(page.locator(f'input[name="{other_field}"]')).to_be_checked()
            main.uncheck()
            confirm = page.get_by_role(
                "dialog", name="Отозвать согласие и выйти из чата?"
            )
            expect(confirm).to_be_visible()
            expect(confirm).to_contain_text("будете исключены")
            confirm.get_by_role("button", name="Нет, остаться", exact=True).click()
            expect(main).to_be_checked()
            expect(page).to_have_url(edit_url)
            main.uncheck()
            expect(confirm).to_be_visible()
            page.keyboard.press("Escape")
            expect(main).to_be_checked()
            main.uncheck()
            expect(confirm).to_be_visible()
            confirm.get_by_role(
                "button", name="Да, отозвать и выйти", exact=True
            ).click()
            expect(page).to_have_url(base + "/chats/")
            expect(
                page.get_by_role("status").filter(has_text="исключены из чата")
            ).to_be_visible()
        finally:
            context.close()
            browser.close()
