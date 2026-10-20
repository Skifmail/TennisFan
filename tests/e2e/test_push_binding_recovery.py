"""Восстановление состояния после поздней загрузки SDK и временных отказов API."""

import json
from os import environ
from pathlib import Path

import pytest
from playwright.sync_api import Route, expect, sync_playwright

from tests.e2e.test_web_push_browser import SDK_MOCK, SITE_ORIGIN

pytestmark = pytest.mark.skipif(
    environ.get("WEB_PUSH_BROWSER_TESTS") != "1", reason="Требуется Chromium"
)


@pytest.mark.parametrize(
    "scenario", ["hydration", "changed", "rate_limit", "transport", "delayed_response"]
)
def test_binding_when_sdk_or_server_recovers_should_keep_consistent_state(
    scenario: str,
) -> None:
    """Проверенная привязка не теряется, лимит и отказ отправки объясняются явно.

    Args:
        scenario: Поздняя загрузка SDK, обновление состояния или временный отказ.
    """
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            page = browser.new_page()
            page.clock.install()
            config = {
                "appId": "test-app",
                "notificationCenter": True,
                "authenticated": True,
                "identityUrl": "/identity",
                "deviceUrl": "/device",
            }
            html = (
                '<script id="web-push-config" type="application/json">'
                + json.dumps(config)
                + '</script><script id="onesignal-sdk"></script><input name="csrfmiddlewaretoken" value="test"><button id="web-push-toggle" disabled></button><p id="web-push-status"></p>'
            )
            page.route(
                "**/*", lambda route: route.fulfill(content_type="text/html", body=html)
            )
            page.route(
                "**/identity",
                lambda route: route.fulfill(
                    json={
                        "externalId": "generation",
                        "pushEnabled": True,
                        "verifiedSubscriptionId": (
                            "test-subscription" if scenario == "hydration" else None
                        ),
                    }
                ),
            )
            calls: list[dict[str, object]] = []
            verified = scenario == "hydration"
            rejected = False
            held: list[Route] = []

            def device(route: Route) -> None:
                """Имитирует существующую серверную привязку и один безопасный отказ.

                Args:
                    route: Изолированный запрос без настоящего провайдера.
                """
                nonlocal verified, rejected
                data = route.request.post_data_json
                assert isinstance(data, dict)
                calls.append(data)
                if data.get("enableAccount") and scenario == "delayed_response":
                    held.append(route)
                    return
                if data.get("active") is False:
                    verified = False
                if (
                    data.get("enableAccount")
                    and scenario in ("rate_limit", "transport")
                    and not rejected
                ):
                    rejected = True
                    route.fulfill(
                        status=429 if scenario == "rate_limit" else 503,
                        headers={"Retry-After": "3"},
                        json={
                            "error": (
                                "confirmation_rate_limited"
                                if scenario == "rate_limit"
                                else "confirmation_unavailable"
                            )
                        },
                    )
                else:
                    route.fulfill(
                        status=202 if data.get("enableAccount") else 200,
                        json={
                            "active": verified or scenario == "changed",
                            "pending": bool(data.get("enableAccount")),
                            "accountPushEnabled": True,
                        },
                    )

            page.route("**/device", device)
            page.goto(SITE_ORIGIN)
            page.evaluate(SDK_MOCK)
            page.evaluate(
                """() => {
                window.handoffArmed = 0;
                window.tennisFanPushHandoff = {beginWaiting: () => {window.handoffArmed++;}, stopWaiting: () => {}};
                window.mockSubscription.addEventListener = (_name, callback) => {window.bindingChanged = callback;};
                window.mockNotificationPermission='granted'; window.mockOneSignal.Notifications.permission=true;
            }"""
            )
            if scenario in ("rate_limit", "transport", "delayed_response"):
                page.evaluate(
                    "() => {window.mockSubscription.id='test-subscription'; window.mockSubscription.optedIn=true;}"
                )
            page.add_script_tag(
                content=(
                    Path(__file__).resolve().parents[2] / "static/js/web_push.js"
                ).read_text()
            )
            page.evaluate(
                "async () => {await window.OneSignalDeferred[0](window.mockOneSignal);}"
            )
            if scenario in ("hydration", "changed"):
                assert not any(data.get("active") is False for data in calls)
                page.evaluate(
                    "() => {window.mockSubscription.id='test-subscription'; window.mockSubscription.optedIn=true; window.bindingChanged();}"
                )
                expect(page.locator("#web-push-toggle")).to_have_text(
                    "Отключить уведомления"
                )
                assert verified or scenario == "changed"
            elif scenario == "delayed_response":
                page.click("#web-push-toggle")
                assert page.evaluate("window.handoffArmed") > 0
                assert len(held) == 1
                held[0].fulfill(
                    status=202,
                    json={"active": False, "pending": True, "accountPushEnabled": True},
                )
                expect(page.locator("#web-push-status")).to_contain_text("подтвердите")
            else:
                expect(page.locator("#web-push-toggle")).to_have_text(
                    "Подтвердить подключение"
                )
                page.click("#web-push-toggle")
                expect(page.locator("#web-push-status")).to_contain_text(
                    "Подождите"
                    if scenario == "rate_limit"
                    else "Не удалось отправить подтверждение"
                )
                expect(page.locator("#web-push-toggle")).to_be_disabled()
                page.clock.fast_forward(3100)
                expect(page.locator("#web-push-toggle")).to_be_enabled()
                page.click("#web-push-toggle")
                expect(page.locator("#web-push-status")).to_contain_text("подтвердите")
                assert page.evaluate("window.pushCalls.optIn") == 0
                assert page.evaluate("window.pushCalls.optOut") == 0
        finally:
            browser.close()
