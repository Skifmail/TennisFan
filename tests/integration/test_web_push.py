"""Проверки публичных файлов и подключения OneSignal в шаблонах."""

from collections.abc import Iterator
from typing import Final

import pytest
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, override_settings
from pytest_mock import MockerFixture

from apps.core.web_push import (
    ONESIGNAL_DIRECT_SDK_URL,
    ONESIGNAL_PAGE_SDK_URL,
    web_push_context,
)

APP_ID: Final = "c6e46e19-f9f8-4df3-b53d-44f49da24af2"
SDK_URL: Final = "https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js"


@pytest.fixture(autouse=True)
def push_settings() -> Iterator[None]:
    """Изолирует настройки публичных файлов от окружения разработчика.

    Yields:
        Изолированное окружение без middleware с побочными эффектами.
    """
    with override_settings(
        ONESIGNAL_APP_ID=APP_ID,
        ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru",
        ALLOWED_HOSTS=["*"],
        MIDDLEWARE=[],
    ):
        yield


def test_worker_when_anonymous_get_should_return_root_javascript(
    client: Client,
) -> None:
    """Worker доступен без входа, редиректов и долгого кэширования."""
    response = client.get(
        "/OneSignalSDKWorker.js", secure=True, HTTP_HOST="tennisfan.ru"
    )
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/javascript")
    body = response.content.decode()
    assert body.startswith(
        'importScripts("/static/js/push_confirmation_handoff.js", "/static/js/push_worker_navigation.js");\n'
    )
    assert body.endswith(
        'importScripts("https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.sw.js");\n'
    )
    assert "no-cache" in response["Cache-Control"]
    assert response["Service-Worker-Allowed"] == "/"


@pytest.mark.parametrize("path", ["/OneSignalSDKWorker.js", "/manifest.webmanifest"])
def test_public_files_when_post_should_reject_method(client: Client, path: str) -> None:
    """Файлы установки не принимают изменение состояния через POST."""
    assert client.post(path, secure=True, HTTP_HOST="tennisfan.ru").status_code == 405


def test_manifest_when_get_should_describe_installable_site(client: Client) -> None:
    """Манифест задаёт стабильную идентичность и режим приложения для iPhone."""
    response = client.get(
        "/manifest.webmanifest", secure=True, HTTP_HOST="tennisfan.ru"
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "application/manifest+json"
    manifest = response.json()
    assert manifest["name"] == "TennisFan"
    assert manifest["id"] == "/"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["icons"][0]["src"].startswith("/static/")
    assert manifest["icons"][0]["sizes"] == "1000x1000"


def test_worker_when_integration_disabled_should_remain_available(
    client: Client,
) -> None:
    """Выключение новых подписок не ломает worker существующих подписчиков."""
    with override_settings(ONESIGNAL_APP_ID=""):
        response = client.get("/OneSignalSDKWorker.js")
    assert response.status_code == 200


@pytest.mark.parametrize("enabled", [True, False])
def test_base_when_enabled_changes_should_conditionally_load_sdk(enabled: bool) -> None:
    """Общий шаблон подключает SDK единожды и содержит управление подпиской."""
    request = RequestFactory().get("/", secure=True, HTTP_HOST="tennisfan.ru")
    with override_settings(ONESIGNAL_APP_ID=APP_ID if enabled else ""):
        html = render_to_string("base.html", web_push_context(request))
    assert html.count(SDK_URL) == int(enabled)
    assert ('rel="manifest"' in html) is enabled
    assert ('id="web-push-toggle"' in html) is enabled
    assert (APP_ID in html) is enabled
    if enabled:
        assert 'aria-live="polite"' in html
        assert "iOS 16.4" in html
        assert html.index("js/web_push.js") < html.index(SDK_URL)


@pytest.mark.parametrize(
    ("user_agent", "expected_url"),
    [
        (
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 YaBrowser/26.8.0.0 "
            "Mobile Safari/537.36",
            ONESIGNAL_DIRECT_SDK_URL,
        ),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) YaBrowser/26.8.0.0 "
            "Mobile/15E148 Safari/604.1",
            ONESIGNAL_PAGE_SDK_URL,
        ),
        (
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
            ONESIGNAL_PAGE_SDK_URL,
        ),
    ],
)
def test_base_when_yandex_android_should_load_sdk_without_loader(
    user_agent: str, expected_url: str
) -> None:
    """Шаблон подключает прямой SDK для Yandex на Android и десктопе.

    Args:
        user_agent: Браузер, который открыл страницу.
        expected_url: Ожидаемый адрес скрипта OneSignal.
    """
    request = RequestFactory().get(
        "/", secure=True, HTTP_HOST="tennisfan.ru", HTTP_USER_AGENT=user_agent
    )
    html = render_to_string("base.html", web_push_context(request))
    assert f'src="{expected_url}"' in html
    assert html.count('id="onesignal-sdk"') == 1


def test_context_when_registered_should_be_available_to_all_pages() -> None:
    """Контекст интеграции зарегистрирован для общего шаблона сайта."""
    from django.conf import settings

    options = settings.TEMPLATES[0]["OPTIONS"]
    assert isinstance(options, dict)
    assert "apps.core.web_push.web_push_context" in options["context_processors"]


@pytest.mark.parametrize("path", ["/OneSignalSDKWorker.js", "/manifest.webmanifest"])
def test_public_files_when_head_should_return_headers_without_body(
    client: Client,
    path: str,
) -> None:
    """Проверка наличия публичного файла не требует скачивания его содержимого."""
    response = client.head(path, secure=True, HTTP_HOST="tennisfan.ru")
    assert response.status_code == 200
    assert response.content == b""


def test_manifest_when_static_storage_hashes_names_should_use_resolved_icon_url(
    client: Client,
    mocker: MockerFixture,
) -> None:
    """Production-хранилище вправе добавлять хэш в имя иконки манифеста."""
    icon_url = "/static/images/favicon.example.png"
    mocker.patch("apps.core.web_push.staticfiles_storage.url", return_value=icon_url)
    response = client.get("/manifest.webmanifest")
    assert response.json()["icons"][0]["src"] == icon_url


@pytest.mark.django_db
def test_auth_when_primary_origin_should_render_sdk_through_context_processors(
    client: Client,
) -> None:
    """Настоящая страница входа получает интеграцию через общий контекст Django."""
    with override_settings(
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ]
    ):
        response = client.get(
            "/users/auth/?mode=login", secure=True, HTTP_HOST="tennisfan.ru"
        )
    assert response.status_code == 200
    html = response.content.decode()
    assert html.count(SDK_URL) == 1
    assert 'id="web-push-toggle"' in html
