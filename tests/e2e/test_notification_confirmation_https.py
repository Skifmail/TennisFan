"""Проверка настоящей формы подтверждения с HTTPS и включённой CSRF-защитой."""

from concurrent.futures import ThreadPoolExecutor
from os import environ
from pathlib import Path
from typing import Final, cast
from urllib.parse import urlsplit

import pytest
from django.db import connections
from django.http import HttpResponse
from django.test import Client, override_settings
from playwright.sync_api import Route, expect, sync_playwright
from pytest_mock import MockerFixture

from apps.notifications.models import PushDevice
from tests.integration.test_notification_identity import _start

ORIGIN: Final = "https://tennisfan.ru"
CONFIRM_PATH: Final = "/notification-center/confirm/"
SCRIPT: Final = Path(__file__).resolve().parents[2] / "static/js/push_confirm.js"

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        environ.get("WEB_PUSH_BROWSER_TESTS") != "1",
        reason="Требуются Chromium и WEB_PUSH_BROWSER_TESTS=1",
    ),
]


@override_settings(
    NOTIFICATION_CENTER_ENABLED=True,
    ONESIGNAL_REST_API_KEY="test-not-a-key",
    ONESIGNAL_SITE_ORIGIN=ORIGIN,
    ALLOWED_HOSTS=["testserver", "tennisfan.ru"],
    SECURE_SSL_REDIRECT=False,
)
def test_confirmation_when_https_browser_submits_should_pass_real_csrf(
    client: Client, mocker: MockerFixture
) -> None:
    """Настоящий Chromium отправляет форму без Origin null, не раскрывая код в GET.

    Args:
        client: Авторизованный тестовый клиент для подготовки подписки.
        mocker: Изолированный транспорт без реальных push.
    """
    _, code = _start(client, mocker)
    protected = Client(enforce_csrf_checks=True)
    protected.cookies.update(client.cookies)
    submitted_headers: list[dict[str, str]] = []

    def application(route: Route) -> None:
        if urlsplit(route.request.url).path != CONFIRM_PATH:
            route.fulfill(content_type="text/javascript", body=SCRIPT.read_text())
            return
        headers = route.request.all_headers()
        method = route.request.method
        data = route.request.post_data or ""
        if method == "POST":
            submitted_headers.append(headers)

        def dispatch() -> HttpResponse:
            try:
                request_headers = {
                    "Host": "tennisfan.ru",
                    **{
                        key: value
                        for key, value in headers.items()
                        if key in ("origin", "referer")
                    },
                }
                return cast(
                    HttpResponse,
                    protected.generic(
                        method,
                        CONFIRM_PATH,
                        data=data,
                        content_type="application/x-www-form-urlencoded",
                        secure=True,
                        headers=request_headers,
                    ),
                )
            finally:
                connections.close_all()

        # ORM работает вне asyncio-потока Playwright; реальные middleware сохранены.
        response = executor.submit(dispatch).result()
        route.fulfill(
            status=response.status_code,
            headers=dict(response.items()),
            body=response.content,
        )

    with ThreadPoolExecutor(max_workers=1) as executor, sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            page = browser.new_page()
            page.route("**/*", application)
            page.goto(ORIGIN + CONFIRM_PATH + "#code=" + code)
            expect(page.locator('input[name="code"]')).to_have_value(code)
            assert code not in page.url
            page.get_by_role("button", name="Подтвердить подключение").click()
            expect(page.get_by_role("heading")).to_have_text("Уведомления подключены")
            assert submitted_headers[0]["origin"] == ORIGIN
        finally:
            browser.close()
    assert PushDevice.objects.get().active
