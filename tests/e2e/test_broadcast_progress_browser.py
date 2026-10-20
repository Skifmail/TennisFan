"""Живые счётчики рассылки без перезагрузки страницы и реальных отправок."""

from os import environ
from pathlib import Path

import pytest
from playwright.sync_api import Route, expect, sync_playwright

pytestmark = pytest.mark.skipif(
    environ.get("WEB_PUSH_BROWSER_TESTS") != "1", reason="Нужен Chromium"
)


def test_broadcast_progress_when_status_changes_should_refresh_and_stop_on_completion() -> (
    None
):
    """Обновляет цифры, поясняет сетевой отказ и прекращает опрос после завершения."""
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            page = browser.new_page()
            page.clock.install()
            html = '<section id="broadcast-progress" data-progress-url="/progress" data-terminal="false"><p data-progress-stage>Ожидает запуска</p><span data-progress-users>0</span><span data-progress-inbox>0</span><span data-channel="push" data-state="accepted">0</span><button id="broadcast-progress-refresh">Обновить статус</button><p data-progress-refresh></p></section>'
            page.route(
                "**/*", lambda route: route.fulfill(content_type="text/html", body=html)
            )
            calls: list[str] = []

            def state(route: Route) -> None:
                """Имитирует один временный отказ и завершение без доставки сообщений.

                Args:
                    route: Локально перехваченный запрос сводки.
                """
                calls.append(route.request.url)
                if len(calls) == 1:
                    route.fulfill(status=503, json={"error": "temporary"})
                else:
                    route.fulfill(
                        json={
                            "stage": "Завершена",
                            "terminal": True,
                            "counters_complete": True,
                            "processed_users": 12,
                            "inbox_created": 12,
                            "channels": {"push": {"accepted": 4}},
                            "as_of": "2026-10-20T10:00:00Z",
                        }
                    )

            page.route("**/progress", state)
            page.goto("https://tennisfan.ru/")
            page.add_script_tag(
                content=(
                    Path(__file__).resolve().parents[2]
                    / "static/js/notification_admin.js"
                ).read_text()
            )
            page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
            page.locator("#broadcast-progress-refresh").click()
            expect(page.locator("[data-progress-refresh]")).to_contain_text(
                "Не удалось обновить"
            )
            page.clock.fast_forward(5000)
            expect(page.locator("[data-progress-stage]")).to_have_text("Завершена")
            expect(page.locator("[data-progress-inbox]")).to_have_text("12")
            expect(page.locator('[data-state="accepted"]')).to_have_text("4")
            page.clock.fast_forward(15000)
            assert len(calls) == 2
        finally:
            browser.close()
