"""Публичная конфигурация и файлы браузерных уведомлений OneSignal."""

from typing import Final

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_safe

WORKER_SCRIPT: Final = (
    'importScripts("https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.sw.js");\n'
)


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
    return {
        "web_push_enabled": enabled,
        "web_push_config": {"appId": app_id} if enabled else {},
    }


@require_safe
def onesignal_worker(request: HttpRequest) -> HttpResponse:
    """Отдаёт worker с корневой областью действия без авторизации.

    Args:
        request: GET- или HEAD-запрос браузера.

    Returns:
        JavaScript с запретом использования устаревшей версии без проверки.
    """
    response = HttpResponse(WORKER_SCRIPT, content_type="application/javascript")
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
