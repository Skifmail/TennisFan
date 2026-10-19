"""Переход в открытой PWA при неработающем WindowClient.navigate на iOS."""

from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from os import environ
from pathlib import Path
from threading import Thread
from typing import Final

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT: Final = Path(__file__).resolve().parents[2]
TARGET: Final = "/notification-center/confirm/#code=" + "a" * 43
pytestmark = pytest.mark.skipif(
    environ.get("WEB_PUSH_BROWSER_TESTS") != "1", reason="Нужен локальный Chromium"
)


class NavigationHandler(SimpleHTTPRequestHandler):
    """Отдаёт тестовые страницы и настоящий JavaScript без внешних запросов.

    Attributes:
        path: Локальный путь запроса без реальных секретов.
    """

    def log_message(self, format: str, *args: object) -> None:
        """Не записывает адреса тестовых запросов.

        Args:
            format: Формат стандартного HTTP-сервера.
            *args: Неиспользуемые значения журнала.
        """

    def do_GET(self) -> None:
        """Отдаёт стенд с исходной страницей, worker и неподвижным SDK."""
        if self.path.startswith("/OneSignalSDKWorker.js"):
            text = """
Object.defineProperty(navigator, 'userAgent', {value: 'iPhone'});
self.sdkClicks = 0;
importScripts('/static/js/push_worker_navigation.js');
self.addEventListener('notificationclick', () => {self.sdkClicks++;});
"""
            mime = "application/javascript"
        elif self.path.startswith("/static/js/"):
            return super().do_GET()
        else:
            text = '<h1>Test page</h1><script src="/static/js/push_navigation.js"></script>'
            mime = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.end_headers()
        self.wfile.write(text.encode())


@pytest.fixture
def navigation_origin() -> Iterator[str]:
    """Запускает изолированный HTTP-origin, доверенный для локального worker.

    Yields:
        Адрес локального стенда без настоящих пользователей и подписок.
    """
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(NavigationHandler, directory=str(ROOT))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("native_failure", ["reject", "no_navigation"])
def test_ios_click_when_native_navigation_fails_should_navigate_through_page(
    navigation_origin: str, native_failure: str
) -> None:
    """Настоящие worker и страница переходят через postMessage при отказе iOS API.

    Args:
        navigation_origin: Изолированный тестовый сервер.
        native_failure: Исключение API либо успешный ответ без смены страницы.
    """
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            page = browser.new_page()
            page.goto(navigation_origin)
            with page.context.expect_event("serviceworker") as found:
                page.evaluate(
                    "navigator.serviceWorker.register('/OneSignalSDKWorker.js')"
                )
            worker = found.value
            page.evaluate("navigator.serviceWorker.ready")
            page.wait_for_function("navigator.serviceWorker.controller !== null")
            result = worker.evaluate(
                """async ({target, failure}) => {
                    WindowClient.prototype.focus = async function () {return this;};
                    WindowClient.prototype.navigate = async function () {
                        if (failure === 'reject') throw new TypeError('iOS navigation failed');
                        return this;
                    };
                    const event = new Event('notificationclick');
                    const promises = [];
                    event.notification = {data: {launchURL: target}, close() {}};
                    event.waitUntil = promise => promises.push(promise);
                    self.dispatchEvent(event);
                    await Promise.all(promises);
                    return {sdkClicks: self.sdkClicks};
                }""",
                {"target": navigation_origin + TARGET, "failure": native_failure},
            )
            expect(page).to_have_url(navigation_origin + TARGET)
            assert result == {"sdkClicks": 0}
        finally:
            browser.close()
