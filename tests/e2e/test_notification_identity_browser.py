"""Браузерная проверка JWT-привязки без запросов к настоящему провайдеру."""

import json
from os import environ
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.e2e.test_web_push_browser import SDK_MOCK, SITE_ORIGIN

pytestmark = pytest.mark.skipif(
    environ.get("WEB_PUSH_BROWSER_TESTS") != "1",
    reason="Требуются Chromium и WEB_PUSH_BROWSER_TESTS=1",
)


@pytest.mark.parametrize(
    "authenticated,identity_ok,account_push",
    [(True, True, True), (True, True, False), (False, True, True), (True, False, True)],
)
def test_binding_when_session_changes_should_login_only_verified_alias(
    authenticated: bool, identity_ok: bool, account_push: bool
) -> None:
    """Аноним выходит из SDK, ошибка JWT не включает персональную подписку."""
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        page = browser.new_page()
        config = {
            "appId": "test-app-id",
            "notificationCenter": True,
            "authenticated": authenticated,
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
                status=200 if identity_ok else 503,
                json={
                    "externalId": "server-alias",
                    "jwt": "test-jwt",
                    "pushEnabled": account_push,
                },
            ),
        )
        page.route(
            "**/device",
            lambda route: route.fulfill(
                json={
                    "active": True,
                    "accountPushEnabled": account_push
                    or route.request.post_data_json.get("enableAccount", False),
                }
            ),
        )
        page.goto(SITE_ORIGIN)
        page.evaluate(SDK_MOCK)
        identity_mock = """() => {
            window.identityCalls = [];
            window.mockOneSignal.login = async (id, token) => {window.identityCalls.push(['login', id, token]);};
            window.mockOneSignal.logout = async () => {window.identityCalls.push(['logout']);};
        }"""
        page.evaluate(identity_mock)
        script = (
            Path(__file__).resolve().parents[2] / "static/js/web_push.js"
        ).read_text()
        page.add_script_tag(content=script)
        page.evaluate(
            "async () => {await window.OneSignalDeferred[0](window.mockOneSignal);}"
        )
        if not authenticated:
            assert page.evaluate("window.identityCalls") == [["logout"]]
            expect(page.locator("#web-push-status")).to_contain_text("Войдите")
            expect(page.locator("#web-push-toggle")).to_be_disabled()
        elif not identity_ok:
            assert page.evaluate("window.identityCalls") == []
            expect(page.locator("#web-push-status")).to_contain_text("недоступен")
        else:
            assert page.evaluate("window.identityCalls") == [
                ["login", "server-alias", "test-jwt"]
            ]
            expect(page.locator("#web-push-toggle")).to_be_enabled()
            if not account_push:
                expect(page.locator("#web-push-status")).to_contain_text("аккаунта")
                page.click("#web-push-toggle")
                expect(page.locator("#web-push-status")).to_have_text(
                    "Уведомления включены."
                )
                assert page.evaluate("window.pushCalls.optOut") == 0
        browser.close()
