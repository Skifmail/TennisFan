"""Проверки доступности публичной конфигурации OneSignal."""

from typing import Final
from urllib.parse import urlsplit

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory, override_settings
from django.urls import resolve

from apps.core.web_push import web_push_context

APP_ID: Final = "c6e46e19-f9f8-4df3-b53d-44f49da24af2"
SITE_ORIGIN: Final = "https://tennisfan.ru"
SDK_URL: Final = "https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js"


@pytest.mark.parametrize(
    ("url", "enabled"),
    [
        (SITE_ORIGIN + "/", True),
        (SITE_ORIGIN + "/users/notifications/", True),
        ("https://www.tennisfan.ru/", False),
        ("https://tennistop.ru/", False),
        ("http://tennisfan.ru/", False),
        ("http://localhost:8000/", False),
    ],
)
def test_context_when_origin_changes_should_enable_only_exact_origin(
    url: str,
    enabled: bool,
) -> None:
    """Один App ID используется только на одном разрешённом HTTPS-origin."""
    with override_settings(
        ONESIGNAL_APP_ID=APP_ID, ONESIGNAL_SITE_ORIGIN=SITE_ORIGIN, ALLOWED_HOSTS=["*"]
    ):
        parsed = urlsplit(url)
        request = RequestFactory().get(
            parsed.path, secure=parsed.scheme == "https", HTTP_HOST=parsed.netloc
        )
        context = web_push_context(request)
    assert context["web_push_enabled"] is enabled
    assert context["web_push_config"] == ({"appId": APP_ID} if enabled else {})


def test_context_when_app_id_empty_should_disable_sdk() -> None:
    """Пустой App ID отключает загрузку интеграции."""
    with override_settings(
        ONESIGNAL_APP_ID="", ONESIGNAL_SITE_ORIGIN=SITE_ORIGIN, ALLOWED_HOSTS=["*"]
    ):
        context = web_push_context(
            RequestFactory().get("/", secure=True, HTTP_HOST="tennisfan.ru")
        )
    assert context == {"web_push_enabled": False, "web_push_config": {}}


@override_settings(
    ONESIGNAL_APP_ID=APP_ID,
    ONESIGNAL_SITE_ORIGIN=SITE_ORIGIN,
    NOTIFICATION_CENTER_ENABLED=True,
    ALLOWED_HOSTS=["*"],
)
def test_sdk_when_center_enabled_should_load_only_on_notifications_page() -> None:
    """Тяжёлый SDK не замедляет остальные страницы, но манифест остаётся везде."""
    for path, sdk_count in (("/", 0), ("/users/notifications/", 1)):
        request = RequestFactory().get(path, secure=True, HTTP_HOST="tennisfan.ru")
        request.resolver_match = resolve(path)
        html = render_to_string("base.html", web_push_context(request))
        assert html.count(SDK_URL) == sdk_count
        assert 'rel="manifest"' in html
        if sdk_count:
            assert "js/web_push.js" in html
