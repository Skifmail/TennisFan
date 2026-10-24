"""Браузерные проверки подписки без обращений к внешнему OneSignal."""

from collections.abc import Iterator
from os import environ
from pathlib import Path
from typing import Final

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from playwright.sync_api import Page, expect, sync_playwright

pytestmark = pytest.mark.skipif(
    environ.get("WEB_PUSH_BROWSER_TESTS") != "1",
    reason="Для браузерных проверок нужны WEB_PUSH_BROWSER_TESTS=1 и Chromium",
)

APP_ID: Final = "c6e46e19-f9f8-4df3-b53d-44f49da24af2"
SITE_ORIGIN: Final = "https://tennisfan.ru"
SDK_MOCK: Final = """
() => {
    const calls = window.pushCalls = {init: [], permission: 0, optIn: 0, optOut: 0};
    const listeners = {};
    window.nativeNotificationPermissionDescriptor = Object.getOwnPropertyDescriptor(Notification, 'permission');
    window.mockNotificationPermission = 'default';
    Object.defineProperty(Notification, 'permission', {
        configurable: true,
        get: () => window.mockNotificationPermission,
    });
    window.failPush = false;
    window.mockSubscription = {
        id: null,
        optedIn: false,
        addEventListener: (name, callback) => { listeners[name] = callback; },
        optIn: async () => {
            calls.optIn++;
            if (window.failPush) throw new Error('offline');
            window.mockSubscription.id = 'test-subscription';
            window.mockSubscription.optedIn = true;
            if (listeners.change) listeners.change();
        },
        optOut: async () => {
            calls.optOut++;
            window.mockSubscription.optedIn = false;
            if (listeners.change) listeners.change();
        },
    };
    window.mockOneSignal = {
        init: async (config) => {
            calls.init.push(config);
            if (window.failPush) throw new Error('init failed');
        },
        Notifications: {
            isPushSupported: () => true,
            permission: false,
            addEventListener: (name, callback) => { listeners[name] = callback; },
            requestPermission: async () => {
                calls.permission++;
                if (window.denyPush) {
                    window.mockNotificationPermission = 'denied';
                    return;
                }
                window.mockNotificationPermission = 'granted';
                window.mockOneSignal.Notifications.permission = true;
            },
        },
        User: {PushSubscription: window.mockSubscription},
    };
}
"""


@pytest.fixture(params=["granted", "denied"])
def native_notification_permission(request: pytest.FixtureRequest) -> str:
    """Возвращает заданное браузерное разрешение для воспроизводимой проверки.

    Args:
        request: Параметризованный запрос pytest.

    Returns:
        Явное исходное разрешение Chromium.
    """
    return str(request.param)


@pytest.fixture
def push_page(native_notification_permission: str) -> Iterator[Page]:
    """Открывает изолированный HTTPS-документ с настоящим блоком подписки.

    Args:
        native_notification_permission: Разрешение настоящего браузерного контекста.

    Yields:
        Страница Chromium без разрешённых внешних запросов.
    """
    controls = render_to_string("includes/web_push_controls.html")
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context()
        context.grant_permissions(
            ["notifications"] if native_notification_permission == "granted" else [],
            origin=SITE_ORIGIN,
        )
        page = context.new_page()
        page.route(
            "**/*",
            lambda route: route.fulfill(
                content_type="text/html; charset=utf-8",
                body=(
                    '<script id="web-push-config" type="application/json">'
                    f'{{"appId": "{APP_ID}"}}</script>'
                    '<script id="onesignal-sdk"></script>'
                    + controls
                    + '<span id="web-push-badge" hidden>✓ Подключено</span>'
                ),
            ),
        )
        page.goto(SITE_ORIGIN)
        page.evaluate(SDK_MOCK)
        try:
            yield page
        finally:
            browser.close()


def test_help_when_controls_rendered_should_describe_yandex_and_chrome(
    push_page: Page,
) -> None:
    """Инструкция доступна сразу и содержит шаги для Яндекс Браузера и Chrome."""
    help_block = push_page.locator("#web-push-android-help")
    expect(help_block).to_be_visible()
    expect(help_block).to_contain_text("Яндекс Браузер")
    expect(help_block).to_contain_text("«Уведомления от сайтов»")
    expect(help_block).to_contain_text("Google Chrome")
    expect(help_block).to_contain_text("«Разрешения» → «Уведомления»")


def test_notification_mock_when_native_permission_differs_should_start_unrequested(
    push_page: Page, native_notification_permission: str
) -> None:
    """Проверяет независимость модельного разрешения SDK от фонового Chromium.

    Args:
        push_page: Страница с подменённым SDK.
        native_notification_permission: Явно настроенное разрешение Chromium.
    """
    native_permission = push_page.evaluate(
        "async () => (await navigator.permissions.query({name: 'notifications'})).state"
    )
    assert native_permission == native_notification_permission
    assert push_page.evaluate("Notification.permission") == "default"
    assert push_page.evaluate("window.mockOneSignal.Notifications.permission") is False
    assert push_page.evaluate("window.pushCalls.permission") == 0


def test_page_when_russian_controls_rendered_should_use_utf8(push_page: Page) -> None:
    """Проверяет корректную кодировку исходного HTML, а не только текста из JS.

    Args:
        push_page: HTTPS-документ с настоящим шаблоном блока подписки.
    """
    assert push_page.evaluate("document.characterSet") == "UTF-8"
    expect(
        push_page.get_by_role("heading", name="Уведомления на устройстве", exact=True)
    ).to_be_visible()


def _load_integration(page: Page) -> None:
    script = Path(settings.BASE_DIR) / "static/js/web_push.js"
    page.add_script_tag(content=script.read_text(encoding="utf-8"))
    page.evaluate(
        "async () => { for (const callback of window.OneSignalDeferred || []) "
        "await callback(window.mockOneSignal); }"
    )


def test_subscription_when_user_clicks_should_subscribe_and_allow_opt_out(
    push_page: Page,
) -> None:
    """Разрешение не запрашивается автоматически; кнопка включает и отключает push."""
    _load_integration(push_page)
    assert push_page.evaluate("window.pushCalls.permission") == 0
    config = push_page.evaluate("window.pushCalls.init[0]")
    assert config["appId"] == APP_ID
    assert config["serviceWorkerPath"] == "OneSignalSDKWorker.js"
    assert config["serviceWorkerParam"] == {"scope": "/"}
    assert config["welcomeNotification"]["disable"] is True
    assert config["promptOptions"]["slidedown"]["prompts"][0]["autoPrompt"] is False
    push_page.get_by_role("button", name="Включить уведомления", exact=True).click()
    expect(push_page.locator("#web-push-status")).to_have_text("Уведомления включены.")
    expect(push_page.locator("#web-push-status")).to_have_attribute(
        "data-state", "connected"
    )
    expect(push_page.locator("#web-push-badge")).to_be_visible()
    assert push_page.evaluate("Notification.permission") == "granted"
    push_page.get_by_role("button", name="Отключить уведомления", exact=True).click()
    expect(push_page.locator("#web-push-status")).to_have_text("Уведомления выключены.")
    expect(push_page.locator("#web-push-status")).to_have_attribute("data-state", "off")
    expect(push_page.locator("#web-push-badge")).to_be_hidden()
    assert push_page.evaluate("Notification.permission") == "granted"
    assert push_page.evaluate("window.pushCalls.optOut") == 1


def test_subscription_when_denied_should_explain_browser_settings(
    push_page: Page,
) -> None:
    """Отказ не повторяет системный запрос и не отображается как успешная подписка."""
    push_page.evaluate("window.denyPush = true")
    _load_integration(push_page)
    push_page.get_by_role("button", name="Включить уведомления", exact=True).click()
    expect(push_page.locator("#web-push-status")).to_contain_text("заблокированы")
    expect(push_page.locator("#web-push-toggle")).to_be_enabled()
    expect(push_page.locator("#web-push-toggle")).to_have_text("Проверить снова")
    assert push_page.evaluate("document.getElementById('web-push-android-help').open")
    expect(push_page.locator("#web-push-android-help")).to_contain_text(
        "Яндекс Браузер"
    )
    expect(push_page.locator("#web-push-android-help")).to_contain_text("Google Chrome")
    assert push_page.evaluate("window.pushCalls.optIn") == 0


def test_subscription_when_permission_already_denied_should_not_prompt(
    push_page: Page,
) -> None:
    """Сохранённый запрет блокирует подписку без нового системного запроса.

    Args:
        push_page: Страница с явно управляемым состоянием разрешения.
    """
    push_page.evaluate("window.mockNotificationPermission = 'denied'")
    _load_integration(push_page)
    expect(push_page.locator("#web-push-status")).to_contain_text("заблокированы")
    expect(push_page.locator("#web-push-toggle")).to_be_enabled()
    expect(push_page.locator("#web-push-toggle")).to_have_text("Проверить снова")
    assert push_page.evaluate("window.pushCalls.permission") == 0
    assert push_page.evaluate("window.pushCalls.optIn") == 0


def test_subscription_when_native_permission_restored_should_respect_browser_policy(
    push_page: Page, native_notification_permission: str
) -> None:
    """Проверяет защиту подписки с настоящим разрешением браузера вместо mock.

    Args:
        push_page: Страница с изолированным SDK.
        native_notification_permission: Разрешённое либо запрещённое состояние Chromium.
    """
    push_page.evaluate(
        "() => { Object.defineProperty(Notification, 'permission', "
        "window.nativeNotificationPermissionDescriptor); }"
    )
    # Headless Shell может запрещать Notification даже при granted в Permissions API.
    native_permission: str = push_page.evaluate("Notification.permission")
    assert native_permission in {"default", "granted", "denied"}
    if native_notification_permission == "denied":
        assert native_permission == "denied"
    _load_integration(push_page)
    if native_permission == "denied":
        expect(push_page.locator("#web-push-toggle")).to_be_enabled()
        expect(push_page.locator("#web-push-toggle")).to_have_text("Проверить снова")
        expect(push_page.locator("#web-push-status")).to_contain_text("заблокированы")
    else:
        expect(push_page.locator("#web-push-toggle")).to_be_enabled()
    assert push_page.evaluate("window.pushCalls.permission") == 0


def test_subscription_when_network_error_should_offer_retry(push_page: Page) -> None:
    """Сбой подписки не ломает страницу и позволяет повторить действие."""
    _load_integration(push_page)
    push_page.evaluate("window.failPush = true")
    push_page.locator("#web-push-toggle").click()
    expect(push_page.locator("#web-push-status")).to_contain_text("Попробуйте ещё раз")
    expect(push_page.locator("#web-push-toggle")).to_be_enabled()
    push_page.evaluate("window.failPush = false")
    push_page.locator("#web-push-toggle").click()
    expect(push_page.locator("#web-push-status")).to_have_text("Уведомления включены.")


def test_sdk_when_init_fails_should_show_unavailable_status(push_page: Page) -> None:
    """Ошибка инициализации показана пользователю вместо вечной загрузки."""
    push_page.evaluate("window.failPush = true")
    _load_integration(push_page)
    expect(push_page.locator("#web-push-status")).to_contain_text("недоступен")
    expect(push_page.locator("#web-push-toggle")).to_be_enabled()
    expect(push_page.locator("#web-push-toggle")).to_have_text("Повторить")


def test_sdk_when_download_fails_should_show_unavailable_status(
    push_page: Page,
) -> None:
    """Блокировка CDN не оставляет неработающую активную кнопку."""
    script = Path(settings.BASE_DIR) / "static/js/web_push.js"
    push_page.add_script_tag(content=script.read_text(encoding="utf-8"))
    push_page.locator("#onesignal-sdk").dispatch_event("error")
    expect(push_page.locator("#web-push-status")).to_contain_text("недоступен")
    expect(push_page.locator("#web-push-toggle")).to_be_enabled()
    expect(push_page.locator("#web-push-toggle")).to_have_text("Повторить")


@pytest.mark.parametrize("standalone", [False, True])
def test_ios_when_home_screen_state_changes_should_explain_install_or_enable_push(
    push_page: Page,
    standalone: bool,
) -> None:
    """iPhone требует установки; установленное приложение может подписаться."""
    push_page.evaluate(
        """(standalone) => {
            Object.defineProperty(navigator, 'userAgent', {value: 'iPhone'});
            Object.defineProperty(navigator, 'standalone', {value: standalone});
        }""",
        standalone,
    )
    _load_integration(push_page)
    if standalone:
        expect(push_page.locator("#web-push-toggle")).to_be_enabled()
        expect(push_page.locator("#web-push-android-help")).to_be_hidden()
        expect(push_page.locator("#web-push-ios-help")).to_be_hidden()
    else:
        expect(push_page.locator("#web-push-ios-help")).to_be_visible()
        expect(push_page.locator("#web-push-ios-help")).to_contain_text(
            "Откройте сайт в Safari"
        )
        expect(push_page.locator("#web-push-ios-help")).to_contain_text(
            "Открыть как веб-приложение"
        )
        expect(push_page.locator("#web-push-ios-help img")).to_have_count(4)
        assert push_page.evaluate("document.getElementById('web-push-ios-help').open")
        expect(push_page.locator("#web-push-android-help")).to_be_hidden()
        expect(push_page.locator("#web-push-toggle")).to_be_hidden()
        assert push_page.evaluate("window.pushCalls.init.length") == 0


def test_sdk_when_browser_unsupported_should_explain_limitation(
    push_page: Page,
) -> None:
    """Неподдерживаемый браузер не предлагает невозможную подписку."""
    push_page.evaluate(
        "window.mockOneSignal.Notifications.isPushSupported = () => false"
    )
    _load_integration(push_page)
    expect(push_page.locator("#web-push-status")).to_contain_text("не поддерживает")
    expect(push_page.locator("#web-push-toggle")).to_be_hidden()


def test_subscription_when_already_subscribed_should_show_opt_out(
    push_page: Page,
) -> None:
    """Повторное открытие страницы не создаёт новую подписку."""
    script = """() => {
            window.mockNotificationPermission = 'granted';
            window.mockOneSignal.Notifications.permission = true;
            window.mockSubscription.optedIn = true;
            window.mockSubscription.id = 'existing-subscription';
        }"""
    push_page.evaluate(script)
    _load_integration(push_page)
    expect(
        push_page.get_by_role("button", name="Отключить уведомления")
    ).to_be_enabled()
    assert push_page.evaluate("window.pushCalls.optIn") == 0


def test_subscription_when_registration_pending_should_wait_for_subscription_id(
    push_page: Page,
) -> None:
    """Разрешение без идентификатора подписки не считается успешной подпиской."""
    push_page.evaluate(
        "window.mockNotificationPermission = 'granted'; "
        "window.mockOneSignal.Notifications.permission = true; "
        "window.mockSubscription.optedIn = true"
    )
    _load_integration(push_page)
    expect(push_page.locator("#web-push-status")).to_contain_text("подключается")
    expect(push_page.locator("#web-push-toggle")).to_be_enabled()
    expect(push_page.locator("#web-push-toggle")).to_have_text("Проверить снова")


def test_subscription_when_opted_out_with_permission_should_reenable_without_prompt(
    push_page: Page,
) -> None:
    """Повторная подписка использует ранее выданное разрешение браузера."""
    push_page.evaluate(
        "window.mockNotificationPermission = 'granted'; "
        "window.mockOneSignal.Notifications.permission = true"
    )
    _load_integration(push_page)
    push_page.locator("#web-push-toggle").click()
    expect(push_page.locator("#web-push-status")).to_have_text("Уведомления включены.")
    assert push_page.evaluate("window.pushCalls.permission") == 0


def test_sdk_when_download_stalls_should_stop_loading_indicator(
    push_page: Page,
) -> None:
    """Таймаут CDN заменяет бесконечную загрузку понятным сообщением."""
    push_page.evaluate(
        "window.setTimeout = (callback) => { window.pushTimeout = callback; return 1; }"
    )
    script = Path(settings.BASE_DIR) / "static/js/web_push.js"
    push_page.add_script_tag(content=script.read_text(encoding="utf-8"))
    push_page.evaluate("window.pushTimeout()")
    expect(push_page.locator("#web-push-status")).to_contain_text("недоступен")
    expect(push_page.locator("#web-push-toggle")).to_be_enabled()
    expect(push_page.locator("#web-push-toggle")).to_have_text("Повторить")


def test_ipad_when_desktop_user_agent_should_still_explain_install(
    push_page: Page,
) -> None:
    """iPad с настольным User-Agent также нуждается в установке на главный экран."""
    script = """() => {
            Object.defineProperty(navigator, 'platform', {value: 'MacIntel'});
            Object.defineProperty(navigator, 'maxTouchPoints', {value: 5});
        }"""
    push_page.evaluate(script)
    _load_integration(push_page)
    expect(push_page.locator("#web-push-ios-help")).to_be_visible()
    assert push_page.evaluate("window.pushCalls.init.length") == 0


def test_controls_when_mobile_viewport_should_fit_screen(push_page: Page) -> None:
    """Блок подписки остаётся читаемым и доступным на узком экране."""
    push_page.set_viewport_size({"width": 390, "height": 844})
    push_page.add_style_tag(
        path=str(Path(settings.BASE_DIR) / "static/css/style.min.css")
    )
    _load_integration(push_page)
    expect(push_page.locator("#web-push-toggle")).to_be_visible()
    assert push_page.evaluate("document.documentElement.scrollWidth <= innerWidth")
