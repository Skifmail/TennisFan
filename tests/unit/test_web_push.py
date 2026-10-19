"""Проверки доступности публичной конфигурации OneSignal."""

from typing import Final
from urllib.parse import urlsplit

import pytest
from django.test import RequestFactory, override_settings

from apps.core.web_push import web_push_context

APP_ID: Final = "c6e46e19-f9f8-4df3-b53d-44f49da24af2"
SITE_ORIGIN: Final = "https://tennisfan.ru"


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
