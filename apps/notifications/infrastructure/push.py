"""Ограниченный транспорт OneSignal: только проверенные подписки устройств."""

from typing import Final

import requests
from django.conf import settings

from ..domain.rules import NotificationError

API_URL: Final = "https://api.onesignal.com/notifications"
HTTP_TIMEOUT: Final = (3.0, 10.0)
PUSH_TTL_SECONDS: Final = 3600


class PushTransportError(NotificationError):
    """Временная ошибка транспорта, допускающая идемпотентный повтор.

    Attributes:
        args: Только безопасный код ошибки, без ключей и ответа API.
    """


class PushRejectedError(NotificationError):
    """Постоянный отказ или отсутствие доступного подписчика.

    Attributes:
        args: Безопасное объяснение отказа.
    """


def send_push(
    *,
    subscription_ids: list[str],
    title: str,
    body: str,
    url: str,
    key: str,
    ttl_seconds: int = PUSH_TTL_SECONDS,
) -> str:
    """Передаёт персональное сообщение OneSignal без возможности массового сегмента.

    Args:
        subscription_ids: Проверенные сервером подписки одного пользователя.
        title: Короткий заголовок.
        body: Короткий текст без секретов.
        url: Проверенная HTTPS-ссылка.
        key: Стабильный UUID задания для безопасного повтора.
        ttl_seconds: Срок хранения сообщения у провайдера в секундах.

    Returns:
        ID принятого сообщения, не подтверждение доставки на экран.

    Raises:
        PushTransportError: Timeout, ограничение частоты или серверная ошибка.
        PushRejectedError: Не настроен транспорт, неверный запрос либо нет подписчиков.
    """
    if (
        not subscription_ids
        or not settings.ONESIGNAL_REST_API_KEY
        or not settings.ONESIGNAL_APP_ID
    ):
        raise PushRejectedError("push_not_configured_or_no_devices")
    try:
        response = requests.post(
            API_URL,
            headers={
                "Authorization": f"Key {settings.ONESIGNAL_REST_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "app_id": settings.ONESIGNAL_APP_ID,
                "target_channel": "push",
                "include_subscription_ids": subscription_ids,
                "headings": {"en": title, "ru": title},
                "contents": {"en": body, "ru": body},
                "url": url,
                "idempotency_key": key,
                "ttl": ttl_seconds,
            },
            timeout=HTTP_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise PushTransportError("push_network_error") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise PushTransportError(f"push_http_{response.status_code}")
    if response.status_code != 200:
        raise PushRejectedError(f"push_http_{response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        # requests и simplejson могут поднимать разные подклассы ошибки разбора.
        raise PushTransportError("push_invalid_json") from exc
    if not isinstance(payload, dict) or not payload.get("id"):
        # Пустой id означает, что провайдер не нашёл ни одной живой подписки.
        # Код совпадает с нулевым recipients, чтобы worker отключил мёртвые адреса.
        raise PushRejectedError("push_no_recipients")
    recipients = payload.get("recipients")
    if isinstance(recipients, str) and recipients.strip().isdigit():
        recipients = int(recipients.strip())
    if isinstance(recipients, bool) or not isinstance(recipients, (int, float)):
        # Часть ответов OneSignal не содержит счётчик; id уже подтверждает приём.
        return str(payload["id"])
    if recipients <= 0:
        raise PushRejectedError("push_no_recipients")
    return str(payload["id"])
