"""Публичная конфигурация и файлы браузерных уведомлений OneSignal."""

import json
from typing import Final

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_safe

ONESIGNAL_SDK_VERSION: Final = "160610"
ONESIGNAL_PAGE_SDK_URL: Final = (
    "https://cdn.onesignal.com/sdks/web/v16/"
    f"OneSignalSDK.page.js?v={ONESIGNAL_SDK_VERSION}"
)
ONESIGNAL_DIRECT_SDK_URL: Final = (
    "https://cdn.onesignal.com/sdks/web/v16/"
    f"OneSignalSDK.page.es6.js?v={ONESIGNAL_SDK_VERSION}"
)
YANDEX_BROWSER_MARKERS: Final = ("yabrowser", "yandexbrowser")
IOS_MARKERS: Final = ("iphone", "ipad", "ipod")

WORKER_SCRIPT: Final = (
    'importScripts("https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.sw.js");\n'
)


def _needs_direct_sdk(request: HttpRequest) -> bool:
    """Проверяет, нужен ли обход проверки загрузчика OneSignal для Yandex.

    Загрузчик OneSignal отказывается подключать SDK, если в браузере нет
    собственного свойства ``PushSubscriptionOptions.applicationServerKey``.
    В мобильном Яндекс Браузере это свойство может быть унаследованным, хотя
    Push API работает. Для таких браузеров загружаем сам SDK напрямую.

    Args:
        request: Текущий запрос с User-Agent браузера.

    Returns:
        True для Яндекс Браузера на Android и десктопе.
    """
    user_agent = request.META.get("HTTP_USER_AGENT", "").lower()
    if any(marker in user_agent for marker in IOS_MARKERS):
        return False
    return any(marker in user_agent for marker in YANDEX_BROWSER_MARKERS)


def web_push_context(request: HttpRequest) -> dict[str, object]:
    """Добавляет публичную конфигурацию SDK только для заданного origin.

    Args:
        request: Текущий запрос сайта.

    Returns:
        Признак доступности интеграции и публичный App ID без секретных ключей.
    """
    app_id = settings.ONESIGNAL_APP_ID
    origin = f"{request.scheme}://{request.get_host()}"
    enabled = bool(app_id) and origin == settings.ONESIGNAL_SITE_ORIGIN
    config: dict[str, object] = {"appId": app_id} if enabled else {}
    if enabled and settings.NOTIFICATION_CENTER_ENABLED:
        config.update(
            {
                "notificationCenter": True,
                "authenticated": bool(
                    getattr(getattr(request, "user", None), "is_authenticated", False)
                ),
                "identityUrl": reverse("notification_identity"),
                "deviceUrl": reverse("notification_device"),
                "codeRequestUrl": reverse("notification_code_request"),
                "codeVerifyUrl": reverse("notification_code_verify"),
                "testUrl": reverse("notification_test_push"),
            }
        )
    context: dict[str, object] = {
        "web_push_enabled": enabled,
        "web_push_config": config,
    }
    if settings.NOTIFICATION_CENTER_ENABLED:
        context["notification_center_enabled"] = True
    if (
        enabled
        and settings.NOTIFICATION_CENTER_ENABLED
        and settings.NOTIFICATION_PROMPT_ENABLED
        and getattr(getattr(request, "user", None), "is_authenticated", False)
        and getattr(request.resolver_match, "url_name", None) == "home"
    ):
        from apps.notifications.presentation import should_invite

        context["show_notification_invitation"] = should_invite(request)
    if enabled:
        context["web_push_needed"] = (
            not settings.NOTIFICATION_CENTER_ENABLED
            or getattr(request.resolver_match, "url_name", None) == "notifications"
        )
        context["web_push_sdk_url"] = (
            ONESIGNAL_DIRECT_SDK_URL
            if _needs_direct_sdk(request)
            else ONESIGNAL_PAGE_SDK_URL
        )
    return context


@require_safe
def onesignal_worker(request: HttpRequest) -> HttpResponse:
    """Отдаёт worker с корневой областью действия без авторизации.

    Args:
        request: GET- или HEAD-запрос браузера.

    Returns:
        JavaScript с запретом использования устаревшей версии без проверки.
    """
    handoff = json.dumps(staticfiles_storage.url("js/push_confirmation_handoff.js"))
    helper = json.dumps(staticfiles_storage.url("js/push_worker_navigation.js"))
    response = HttpResponse(
        f"importScripts({handoff}, {helper});\n" + WORKER_SCRIPT,
        content_type="application/javascript",
    )
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


@require_safe
def web_app_manifest(request: HttpRequest) -> JsonResponse:
    """Отдаёт описание веб-приложения TennisFan для установки на телефон.

    Args:
        request: GET- или HEAD-запрос браузера.

    Returns:
        Манифест со стабильной идентичностью, иконкой и режимом standalone.
    """
    return JsonResponse(
        {
            "name": "TennisFan",
            "short_name": "TennisFan",
            "id": "/",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "lang": "ru",
            "icons": [
                {
                    "src": staticfiles_storage.url("images/favicon.png"),
                    "sizes": "1000x1000",
                    "type": "image/png",
                    "purpose": "any",
                }
            ],
        },
        content_type="application/manifest+json",
    )
