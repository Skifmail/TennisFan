"""Браузерная приёмка на двух процессах ASGI с реальным Redis.

Стенд и сессии передаются через CHAT_E2E_URL, CHAT_E2E_SECOND_URL и
CHAT_E2E_SESSION_FILE. Обычный запуск тестов не касается внешнего сервера.
"""

import json
from os import environ
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен изолированный браузерный стенд"
)


def test_chat_when_two_processes_should_deliver_recover_edit_and_escape() -> None:
    """Доставка, восстановление и безопасное отображение работают в браузере."""
    from playwright.sync_api import expect, sync_playwright

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    second = environ["CHAT_E2E_SECOND_URL"]
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        contexts = [
            browser.new_context(viewport={"width": 1280, "height": 1300})
            for _ in range(2)
        ]
        errors: list[str] = []
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
                page.on("pageerror", lambda failure: errors.append(str(failure)))
                page.goto((base if index == 0 else second) + sessions["room_path"])
                expect(page.locator("#chat-status")).to_have_text(
                    "На связи", timeout=15000
                )
                expect(page.locator(".chat-app")).to_have_css("color-scheme", "dark")
                expect(page.locator("#chat-text")).to_have_css(
                    "background-color", "rgba(0, 0, 0, 0)"
                )
                pages.append(page)
            first, other = pages
            unsafe = '<img src=x onerror="window.chatXss=true"> Сыграем?'
            first.locator("#chat-text").fill(unsafe)
            first.locator("#chat-send").click()
            expect(
                other.locator(".city-chat-message-text").filter(has_text=unsafe)
            ).to_have_count(1, timeout=10000)
            assert other.locator(".city-chat-message-text img").count() == 0
            assert other.evaluate("window.chatXss === undefined")
            other.locator(".city-chat-message").last.get_by_role(
                "button", name="Действия с сообщением"
            ).click()
            other.get_by_role("menuitem", name="Ответить", exact=True).click()
            other.locator("#chat-text").fill("Готов сыграть в субботу")
            other.locator("#chat-send").click()
            expect(
                first.locator(".city-chat-message-text").filter(
                    has_text="Готов сыграть в субботу"
                )
            ).to_have_count(1, timeout=10000)
            first.locator(
                '.city-chat-message[data-own="false"][data-deleted="false"]'
            ).last.get_by_role("button", name="Действия с сообщением").click()
            first.get_by_role("menuitem", name="Пожаловаться", exact=True).click()
            report = first.get_by_role("dialog", name="Пожаловаться на сообщение")
            report.get_by_label("Причина жалобы").fill("Проверка интерфейса жалобы")
            report.get_by_role("button", name="Отправить жалобу", exact=True).click()
            expect(report).not_to_be_visible()
            expect(first.locator("#chat-notice")).to_have_text(
                "Жалоба отправлена администрации."
            )
            contexts[1].set_offline(True)
            first.locator('[data-own="true"]').last.get_by_role(
                "button", name="Действия с сообщением"
            ).click()
            first.get_by_role("menuitem", name="Изменить", exact=True).click()
            first.locator("#chat-text").fill("Исправленный вопрос после обрыва связи")
            first.locator("#chat-send").click()
            expect(
                first.locator(".city-chat-message-text").filter(
                    has_text="Исправленный вопрос после обрыва связи"
                )
            ).to_have_count(1)
            contexts[1].set_offline(False)
            # Сохраняем курсор в браузере и восстанавливаем пропущенную правку.
            other.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            expect(
                other.locator(".city-chat-message-text").filter(
                    has_text="Исправленный вопрос после обрыва связи"
                )
            ).to_have_count(1)
            first.locator('[data-own="true"]').last.get_by_role(
                "button", name="Действия с сообщением"
            ).click()
            first.get_by_role("menuitem", name="Удалить", exact=True).click()
            first.get_by_role("dialog").get_by_role(
                "button", name="Удалить сообщение", exact=True
            ).click()
            expect(
                other.locator(".city-chat-message-text").filter(
                    has_text="Сообщение удалено"
                )
            ).to_have_count(1, timeout=10000)
            artifact_dir = Path(
                environ.get(
                    "CHAT_E2E_ARTIFACTS", "/tmp/tennison-chat-browser-artifacts"
                )
            )
            artifact_dir.mkdir(parents=True, exist_ok=True)
            first.evaluate("window.scrollTo(0, 0)")
            first.screenshot(
                path=str(artifact_dir / "chat-desktop.png"), full_page=False
            )
            other.set_viewport_size({"width": 390, "height": 844})
            assert other.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            other.evaluate("window.scrollTo(0, 0)")
            other.screenshot(
                path=str(artifact_dir / "chat-mobile.png"), full_page=False
            )
            assert not errors
        finally:
            for context in contexts:
                context.close()
            browser.close()
