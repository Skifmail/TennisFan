"""Переход в открытой PWA при неработающем WindowClient.navigate на iOS."""

from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from os import environ
from pathlib import Path
from threading import Thread
from typing import Final, cast

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT: Final = Path(__file__).resolve().parents[2]
TARGET: Final = "/notification-center/confirm/#code=" + "a" * 43
pytestmark = pytest.mark.skipif(
    environ.get("WEB_PUSH_BROWSER_TESTS") != "1", reason="Нужен локальный Chromium"
)


class NavigationServer(ThreadingHTTPServer):
    """Считает фактические загрузки документов изолированного стенда.

    Attributes:
        confirmation_loads: Число GET страницы подтверждения.
    """

    confirmation_loads: int = 0


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
try {importScripts('/static/js/push_confirmation_handoff.js');} catch (_) {}
importScripts('/static/js/push_worker_navigation.js');
self.addEventListener('notificationclick', () => {self.sdkClicks++;});
"""
            mime = "application/javascript"
        elif self.path.startswith("/static/js/"):
            return super().do_GET()
        else:
            authenticated = "false" if self.path.startswith("/anonymous") else "true"
            text = (
                '<script id="web-push-config" type="application/json">{"authenticated":'
                + authenticated
                + "}</script><h1>Test page</h1>"
            )
            if self.path.startswith("/notification-center/confirm/"):
                text = "<h1>Protected standalone confirmation</h1>"
                server = cast(NavigationServer, self.server)
                server.confirmation_loads += 1
                text += f"<p id=loaded>{server.confirmation_loads}</p>"
            if not self.path.startswith("/no-bridge"):
                text += '<script src="/static/js/push_confirmation_handoff.js"></script><script src="/static/js/push_navigation.js"></script>'
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
    server = NavigationServer(
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
@pytest.mark.parametrize("opaque_origin", [False, True])
def test_ios_click_when_native_navigation_fails_should_navigate_through_page(
    navigation_origin: str, native_failure: str, opaque_origin: bool
) -> None:
    """Настоящие worker и страница переходят через postMessage при отказе iOS API.

    Args:
        navigation_origin: Изолированный тестовый сервер.
        native_failure: Исключение API либо успешный ответ без смены страницы.
        opaque_origin: Эмулировать пустой origin сообщения при доверенном worker.
    """
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            page = browser.new_page()
            if opaque_origin:
                page.add_init_script(
                    """
                    const add = navigator.serviceWorker.addEventListener.bind(navigator.serviceWorker);
                    navigator.serviceWorker.addEventListener = (name, handler, options) => add(name,
                        name === 'message' ? event => handler({origin: '', source: event.source,
                            data: event.data, ports: event.ports}) : handler, options);
                """
                )
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


@pytest.mark.parametrize("failure", ["reject", "no_navigation"])
def test_ios_click_when_page_does_not_acknowledge_should_try_exact_window(
    navigation_origin: str, failure: str
) -> None:
    """Непринятое сообщение не может молча поглотить нажатие на push.

    Args:
        navigation_origin: Локальный стенд без SDK и отправок.
        failure: Отказ или ложный успех нативной навигации.
    """
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            page = browser.new_page()
            page.goto(navigation_origin + "/no-bridge")
            with page.context.expect_event("serviceworker") as found:
                page.evaluate(
                    "navigator.serviceWorker.register('/OneSignalSDKWorker.js')"
                )
            worker = found.value
            page.evaluate("navigator.serviceWorker.ready")
            result = worker.evaluate(
                """async ({target, failure}) => {
                    WindowClient.prototype.focus = async function () {return this;};
                    WindowClient.prototype.navigate = async function () {
                        if (failure === 'reject') throw new TypeError('iOS navigation failed');
                        return this;
                    };
                    const opened = [];
                    self.clients.openWindow = async url => {opened.push(url); return null;};
                    const event = new Event('notificationclick');
                    const promises = [];
                    event.notification = {data: {launchURL: target}, close() {}};
                    event.waitUntil = promise => promises.push(promise);
                    self.dispatchEvent(event);
                    await Promise.all(promises);
                    return {opened};
                }""",
                {"target": navigation_origin + TARGET, "failure": failure},
            )
            assert result == {"opened": [navigation_origin + TARGET]}
        finally:
            browser.close()


@pytest.mark.parametrize(
    "scenario", ["ghost_client", "ghost_resume", "pending_focus", "same_path"]
)
def test_ios_click_when_delivery_path_unreliable_should_load_real_confirmation(
    navigation_origin: str, scenario: str
) -> None:
    """Проверяет настоящую загрузку документа, а не только смену URL в браузере.

    Args:
        navigation_origin: Изолированный origin с настоящим worker и IndexedDB.
        scenario: Невидимый клиент iOS, зависший фокус или старая форма подтверждения.
    """
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            context = browser.new_context(user_agent="iPhone")
            page = context.new_page()
            page.goto(
                navigation_origin
                + ("/notification-center/confirm/" if scenario == "same_path" else "/")
            )
            if scenario == "same_path":
                expect(page.locator("#loaded")).to_have_text("1")
            with context.expect_event("serviceworker") as found:
                page.evaluate(
                    "navigator.serviceWorker.register('/OneSignalSDKWorker.js')"
                )
            worker = found.value
            page.evaluate("navigator.serviceWorker.ready")
            page.wait_for_function("navigator.serviceWorker.controller !== null")
            if scenario == "ghost_client":
                page.evaluate("window.tennisFanPushHandoff?.beginWaiting()")
            worker.evaluate(
                """({target, scenario}) => {
                self.recoverySettled = false;
                if (scenario.startsWith('ghost_')) {
                    self.clients.matchAll = async () => [];
                    self.clients.openWindow = async () => null;
                } else if (scenario === 'same_path') {
                    WindowClient.prototype.navigate = async function () {return this;};
                } else if (scenario === 'pending_focus') {
                    WindowClient.prototype.focus = () => new Promise(() => {});
                }
                const event = new Event('notificationclick');
                event.notification = {data: {launchURL: target}, close() {}};
                event.waitUntil = promise => promise.then(() => {self.recoverySettled = true;});
                self.dispatchEvent(event);
            }""",
                {"target": navigation_origin + TARGET, "scenario": scenario},
            )
            if scenario == "ghost_resume":
                page.close()
                worker.evaluate(
                    "() => new Promise(resolve => setTimeout(resolve, 1500))"
                )
                page = context.new_page()
                page.goto(navigation_origin)
            expect(page.locator("#loaded")).to_have_text(
                "2" if scenario == "same_path" else "1", timeout=7000
            )
            page.wait_for_function("location.hash.startsWith('#code=')")
            assert worker.evaluate(
                """() => new Promise(resolve => {
                const timer = setInterval(() => {
                    if (self.recoverySettled) {clearInterval(timer); resolve(true);}
                }, 20);
                setTimeout(() => {clearInterval(timer); resolve(false);}, 7000);
            })"""
            )
            assert page.url == navigation_origin + TARGET
        finally:
            browser.close()


@pytest.mark.parametrize("scenario", ["expired", "invalid", "replaced"])
def test_handoff_when_record_invalid_or_replaced_should_not_replay(
    navigation_origin: str, scenario: str
) -> None:
    """Хранилище не воспроизводит просроченный или чужой адрес и бережёт новый клик.

    Args:
        navigation_origin: Изолированный origin для настоящего IndexedDB.
        scenario: Истечение, недопустимый адрес или замена предыдущего клика.
    """
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            page = browser.new_page()
            page.clock.install()
            page.goto(navigation_origin)
            assert page.evaluate("typeof window.tennisFanPushHandoff !== 'undefined'")
            target = navigation_origin + TARGET
            if scenario == "invalid":
                assert not page.evaluate(
                    "url => window.tennisFanPushHandoff.remember(url)",
                    "https://untrusted.example" + TARGET,
                )
                assert page.evaluate("window.tennisFanPushHandoff.read()") is None
            else:
                assert page.evaluate(
                    "url => window.tennisFanPushHandoff.remember(url)", target
                )
                if scenario == "expired":
                    page.clock.fast_forward(300001)
                    assert page.evaluate("window.tennisFanPushHandoff.read()") is None
                else:
                    newer = target.replace("a" * 43, "b" * 43)
                    assert page.evaluate(
                        "url => window.tennisFanPushHandoff.remember(url)", newer
                    )
                    page.evaluate(
                        "url => window.tennisFanPushHandoff.forget(url)", target
                    )
                    assert page.evaluate("window.tennisFanPushHandoff.read()") == newer
                    page.evaluate(
                        "url => window.tennisFanPushHandoff.forget(url)", newer
                    )
                    assert page.evaluate("window.tennisFanPushHandoff.read()") is None
        finally:
            browser.close()


def test_handoff_when_session_anonymous_should_not_redirect_to_login_loop(
    navigation_origin: str,
) -> None:
    """Ожидавший клик не зацикливает переход при истёкшем входе.

    Args:
        navigation_origin: Origin страницы без авторизованной сессии.
    """
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            page = browser.new_page(user_agent="iPhone")
            page.goto(navigation_origin + "/anonymous")
            page.evaluate(
                "url => window.tennisFanPushHandoff.remember(url)",
                navigation_origin + TARGET,
            )
            page.evaluate("window.dispatchEvent(new Event('focus'))")
            page.wait_for_timeout(100)
            assert page.url == navigation_origin + "/anonymous"
        finally:
            browser.close()
