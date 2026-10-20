"""Ввод кода из push в открытом приложении без перехода по уведомлению."""

import json
from os import environ
from pathlib import Path

import pytest
from playwright.sync_api import Route, expect, sync_playwright

from tests.e2e.test_web_push_browser import SDK_MOCK, SITE_ORIGIN

pytestmark = pytest.mark.skipif(
    environ.get("WEB_PUSH_BROWSER_TESTS") != "1", reason="Нужен Chromium"
)


@pytest.mark.parametrize("pending", [False, True])
def test_numeric_code_when_open_app_should_verify_without_navigation(
    pending: bool,
) -> None:
    """Открытая страница принимает код и восстанавливает незавершённый ввод.

    Args:
        pending: Уже отправлен код перед перезагрузкой страницы.
    """
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            page = browser.new_page()
            config = {
                "appId": "test",
                "notificationCenter": True,
                "authenticated": True,
                "identityUrl": "/identity",
                "deviceUrl": "/device",
                "codeRequestUrl": "/request-code",
                "codeVerifyUrl": "/verify-code",
            }
            html = (
                '<script id="web-push-config" type="application/json">'
                + json.dumps(config)
                + '</script><input name="csrfmiddlewaretoken" value="test"><input id="id_push_enabled" type="checkbox"><button id="web-push-toggle"></button><p id="web-push-status"></p><form id="web-push-code-form" hidden><input id="web-push-code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required><button id="web-push-code-submit">Подтвердить код</button><p id="web-push-code-status" role="status"></p></form>'
            )
            page.route(
                "**/*", lambda route: route.fulfill(content_type="text/html", body=html)
            )
            page.route(
                "**/identity",
                lambda route: route.fulfill(
                    json={
                        "externalId": "generation",
                        "pushEnabled": False,
                        "verifiedSubscriptionId": None,
                        "pendingCode": pending,
                    }
                ),
            )
            page.route(
                "**/device",
                lambda route: route.fulfill(
                    json={"active": False, "accountPushEnabled": False}
                ),
            )
            page.route(
                "**/request-code",
                lambda route: route.fulfill(
                    status=202,
                    json={
                        "active": False,
                        "pending": True,
                        "accountPushEnabled": False,
                    },
                ),
            )
            attempts: list[object] = []

            def verify(route: Route) -> None:
                """Принимает только код в теле запроса, не в адресе.

                Args:
                    route: Изолированный запрос без доставки настоящего push.
                """
                data = route.request.post_data_json
                assert isinstance(data, dict)
                attempts.append(data)
                assert route.request.headers["x-csrftoken"] == "test"
                assert "123456" not in route.request.url
                route.fulfill(
                    status=200 if data["code"] == "123456" else 400,
                    json=(
                        {"active": True, "accountPushEnabled": True}
                        if data["code"] == "123456"
                        else {"error": "confirmation_invalid"}
                    ),
                )

            page.route("**/verify-code", verify)
            page.goto(SITE_ORIGIN)
            page.evaluate(SDK_MOCK)
            page.evaluate(
                "() => {window.mockSubscription.id='test-subscription'; window.mockSubscription.optedIn=true; window.mockNotificationPermission='granted'; window.mockOneSignal.Notifications.permission=true;}"
            )
            page.add_script_tag(
                content=(
                    Path(__file__).resolve().parents[2] / "static/js/web_push.js"
                ).read_text()
            )
            page.evaluate(
                "async () => {await window.OneSignalDeferred[0](window.mockOneSignal);}"
            )
            if not pending:
                expect(page.locator("#web-push-code-form")).to_be_hidden()
                page.locator("#web-push-toggle").click()
            expect(page.locator("#web-push-code-form")).to_be_visible()
            page.locator("#web-push-code").fill("654321")
            page.locator("#web-push-code-submit").click()
            expect(page.locator("#web-push-code-status")).to_contain_text(
                "Неверный код"
            )
            page.locator("#web-push-code").fill("123456")
            page.locator("#web-push-code-submit").click()
            expect(page.locator("#web-push-status")).to_have_text(
                "Уведомления включены."
            )
            expect(page.locator("#web-push-code-form")).to_be_hidden()
            expect(page.locator("#id_push_enabled")).to_be_checked()
            assert len(attempts) == 2
            assert page.url == SITE_ORIGIN + "/"
        finally:
            browser.close()
