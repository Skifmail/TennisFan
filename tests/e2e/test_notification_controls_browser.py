"""Управление push, предупреждение об отключении и ненавязчивое приглашение."""

from collections.abc import Iterator
from os import environ
from pathlib import Path

import pytest
from django.template.loader import render_to_string
from playwright.sync_api import Page, expect, sync_playwright

from apps.notifications.forms import PreferenceForm
from tests.e2e.test_web_push_browser import SDK_MOCK, SITE_ORIGIN

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    environ.get("WEB_PUSH_BROWSER_TESTS") != "1", reason="Требуется Chromium"
)


@pytest.fixture
def controls_page() -> Iterator[Page]:
    """Открывает реальную панель с изолированным SDK без внешних сообщений.

    Yields:
        Страница с проверенным устройством и двумя включёнными каналами.
    """
    panel = render_to_string(
        "notifications/_settings_panel.html",
        {
            "csrf_token": "test-csrf",
            "notification_form": PreferenceForm(
                initial={"push_enabled": True, "email_enabled": True}
            ),
            "notification_push_enabled": True,
            "notification_settings_open": True,
        },
    )
    css = (ROOT / "static/css/pages/notification-settings.css").read_text()
    html = (
        '<meta charset="utf-8"><style>body{background:#012b1c;margin:0;padding:16px;color:#e4e4e4;}'
        + css
        + '</style><script id="web-push-config" type="application/json">{"appId":"test-app","notificationCenter":true,"authenticated":true,"identityUrl":"/identity","deviceUrl":"/device"}</script><script id="onesignal-sdk"></script>'
        + panel
    )
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.route(
            "**/*",
            lambda route: route.fulfill(
                content_type="text/html; charset=utf-8", body=html
            ),
        )
        page.route(
            "**/identity",
            lambda route: route.fulfill(
                json={
                    "externalId": "generation",
                    "pushEnabled": True,
                    "verifiedSubscriptionId": "test-subscription",
                }
            ),
        )
        page.route(
            "**/device",
            lambda route: route.fulfill(
                json={
                    "active": bool(route.request.post_data_json.get("active")),
                    "accountPushEnabled": True,
                }
            ),
        )
        page.goto(SITE_ORIGIN)
        page.evaluate(SDK_MOCK)
        page.evaluate(
            """() => {
            window.mockSubscription.id='test-subscription'; window.mockSubscription.optedIn=true;
            window.mockNotificationPermission='granted'; window.mockOneSignal.Notifications.permission=true;
        }"""
        )
        page.add_script_tag(
            content=(ROOT / "static/js/notification_settings.js").read_text()
        )
        page.add_script_tag(content=(ROOT / "static/js/web_push.js").read_text())
        page.evaluate(
            "async () => {await window.OneSignalDeferred[0](window.mockOneSignal);}"
        )
        try:
            yield page
        finally:
            browser.close()


def test_device_when_disable_cancelled_should_keep_push_and_email(
    controls_page: Page,
) -> None:
    """Отмена предупреждения не меняет доставку и не вызывает optOut SDK.

    Args:
        controls_page: Настоящая панель управления устройством.
    """
    controls_page.get_by_role(
        "button", name="Отключить уведомления", exact=True
    ).click()
    dialog = controls_page.locator("#notification-disable-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("личном кабинете")
    assert controls_page.evaluate("window.pushCalls.optOut") == 0
    dialog.get_by_role("button", name="Оставить включёнными").click()
    expect(dialog).not_to_be_visible()
    assert controls_page.evaluate("window.pushCalls.optOut") == 0
    expect(controls_page.locator('[name="email_enabled"]')).to_be_checked()


def test_device_when_disable_confirmed_should_opt_out_only_after_warning(
    controls_page: Page,
) -> None:
    """Подтверждение отключает устройство, но не изменяет выбор email.

    Args:
        controls_page: Настоящая панель управления устройством.
    """
    controls_page.get_by_role(
        "button", name="Отключить уведомления", exact=True
    ).click()
    controls_page.locator("#notification-disable-dialog").get_by_role(
        "button", name="Отключить push", exact=True
    ).click()
    expect(controls_page.locator("#web-push-status")).to_contain_text("выключены")
    assert controls_page.evaluate("window.pushCalls.optOut") == 1
    expect(controls_page.locator('[name="email_enabled"]')).to_be_checked()
    assert controls_page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    controls_page.screenshot(
        path="/tmp/notification-settings-mobile.png", full_page=True
    )


def test_channels_when_disable_cancelled_should_restore_checkbox(
    controls_page: Page,
) -> None:
    """Сохранение выключенного push требует предупреждения, отмена возвращает выбор.

    Args:
        controls_page: Панель с двумя включёнными каналами.
    """
    controls_page.locator('[name="push_enabled"]').uncheck()
    controls_page.get_by_role("button", name="Сохранить выбор").click()
    controls_page.locator("#notification-disable-dialog").get_by_role(
        "button", name="Оставить включёнными"
    ).click()
    expect(controls_page.locator('[name="push_enabled"]')).to_be_checked()
    expect(controls_page.locator('[name="email_enabled"]')).to_be_checked()


def test_invitation_when_later_selected_should_explain_location_and_reappear_on_next_entry() -> (
    None
):
    """«Позже» показывает подсказку, но не сохраняет постоянный отказ.

    Повторное открытие главной не наследует состояние закрытого окна.
    """
    html = render_to_string("notifications/_invitation.html")
    script = (ROOT / "static/js/notification_invitation.js").read_text()
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.route(
                "**/*",
                lambda route: route.fulfill(
                    content_type="text/html; charset=utf-8", body=html
                ),
            )
            page.goto(SITE_ORIGIN)
            page.add_script_tag(content=script)
            dialog = page.locator("#notification-invitation")
            expect(dialog).to_be_visible()
            dialog.get_by_role("button", name="Позже", exact=True).click()
            expect(dialog).to_contain_text("нажмите на своё имя")
            dialog.get_by_role("button", name="Понятно", exact=True).click()
            expect(dialog).not_to_be_visible()
            page.reload()
            page.add_script_tag(content=script)
            expect(page.get_by_role("button", name="Позже", exact=True)).to_be_visible()
        finally:
            browser.close()
