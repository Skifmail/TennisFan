"""Подтверждение Web-подписки без неподдерживаемого JWT и без сетевых отправок."""

import json
from os import environ
from pathlib import Path

import pytest
from playwright.sync_api import Route, expect, sync_playwright

from tests.e2e.test_web_push_browser import SDK_MOCK, SITE_ORIGIN

pytestmark = pytest.mark.skipif(
    environ.get("WEB_PUSH_BROWSER_TESTS") != "1",
    reason="Требуются Chromium и WEB_PUSH_BROWSER_TESTS=1",
)


@pytest.mark.parametrize(
    "state", ["anonymous", "unverified", "verified", "failure", "account_off"]
)
def test_binding_when_state_changes_should_require_confirmation_without_login(
    state: str,
) -> None:
    """SDK не получает alias/JWT, неподтверждённый браузер не объявляется включённым."""
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        page = browser.new_page()
        config = {
            "appId": "test-app",
            "notificationCenter": True,
            "authenticated": state != "anonymous",
            "identityUrl": "/identity",
            "deviceUrl": "/device",
        }
        html = (
            '<script id="web-push-config" type="application/json">'
            + json.dumps(config)
            + '</script><script id="onesignal-sdk"></script><input name="csrfmiddlewaretoken" value="test"><button id="web-push-toggle" disabled></button><p id="web-push-status" role="status"></p>'
        )
        page.route(
            "**/*",
            lambda route: route.fulfill(
                content_type="text/html; charset=utf-8", body=html
            ),
        )
        page.route(
            "**/identity",
            lambda route: route.fulfill(
                status=503 if state == "failure" else 200,
                json={
                    "externalId": "generation",
                    "pushEnabled": state != "account_off",
                    "verifiedSubscriptionId": (
                        "test-subscription"
                        if state in ("verified", "account_off")
                        else None
                    ),
                },
            ),
        )
        requests: list[dict[str, object]] = []

        def device_route(route: Route) -> None:
            """Фиксирует только изолированные запросы к тестовому серверу."""
            data = route.request.post_data_json
            assert isinstance(data, dict)
            requests.append(data)
            route.fulfill(
                status=202 if data.get("enableAccount") else 200,
                json={
                    "active": state == "verified",
                    "pending": bool(data.get("enableAccount")),
                    "accountPushEnabled": state != "account_off",
                },
            )

        page.route("**/device", device_route)
        page.goto(SITE_ORIGIN)
        page.evaluate(SDK_MOCK)
        identity_mock = """() => {
            window.mockSubscription.id = 'test-subscription';
            window.mockSubscription.optedIn = true;
            window.mockNotificationPermission = 'granted';
            window.mockOneSignal.Notifications.permission = true;
            window.identityCalls = [];
            window.mockOneSignal.login = async () => {window.identityCalls.push('login');};
            window.mockOneSignal.logout = async () => {window.identityCalls.push('logout');};
        }"""
        page.evaluate(identity_mock)
        page.add_script_tag(
            content=(
                Path(__file__).resolve().parents[2] / "static/js/web_push.js"
            ).read_text()
        )
        page.evaluate(
            "async () => {await window.OneSignalDeferred[0](window.mockOneSignal);}"
        )
        # Открытая PWA должна перейти по ссылке, а не только получить фокус.
        options = page.evaluate("window.pushCalls.init[0]")
        assert options["notificationClickHandlerMatch"] == "origin"
        assert options["notificationClickHandlerAction"] == "navigate"
        assert page.evaluate("window.identityCalls") == []
        if state == "anonymous":
            expect(page.locator("#web-push-status")).to_contain_text("Войдите")
        elif state == "failure":
            expect(page.locator("#web-push-status")).to_contain_text("недоступен")
        elif state == "verified":
            expect(page.locator("#web-push-toggle")).to_have_text(
                "Отключить уведомления"
            )
        else:
            expect(page.locator("#web-push-status")).not_to_have_text(
                "Уведомления включены."
            )
            page.click("#web-push-toggle")
            expect(page.locator("#web-push-status")).to_contain_text("подтвердите")
            assert any(data.get("enableAccount") is True for data in requests)
            assert page.evaluate("window.pushCalls.optOut") == 0
        browser.close()


@pytest.mark.parametrize("already_open", [False, True])
def test_confirmation_when_opened_from_push_should_keep_secret_out_of_get(
    already_open: bool,
) -> None:
    """Фрагмент удаляется из адресной строки и попадает только в подтверждающий POST.

    Args:
        already_open: Доставить код в уже открытую страницу без перезагрузки.
    """
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        page = browser.new_page()
        urls: list[str] = []

        def document_route(route: Route) -> None:
            """Отдаёт изолированную форму без запросов к провайдеру."""
            urls.append(route.request.url)
            route.fulfill(
                content_type="text/html; charset=utf-8",
                body='<form method="post"><input name="code"><button name="confirm" value="yes">Подтвердить</button></form>',
            )

        page.route("**/*", document_route)
        target = SITE_ORIGIN + "/notification-center/confirm/"
        page.goto(target if already_open else target + "#code=delivered-secret")
        page.add_script_tag(
            content=(
                Path(__file__).resolve().parents[2] / "static/js/push_confirm.js"
            ).read_text()
        )
        if already_open:
            page.evaluate("window.location.hash = 'code=delivered-secret'")
        expect(page.locator('input[name="code"]')).to_have_value("delivered-secret")
        assert "delivered-secret" not in page.url
        assert all("delivered-secret" not in url for url in urls)
        with page.expect_request(lambda request: request.method == "POST") as submitted:
            page.click('button[name="confirm"]')
        assert "code=delivered-secret" in (submitted.value.post_data or "")
        browser.close()
