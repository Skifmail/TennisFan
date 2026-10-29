"""Ограниченный транспорт OneSignal: только проверенные подписки устройств."""

from typing import Final

import requests
from django.conf import settings
from loguru import logger

from ..domain.rules import NotificationError

API_ORIGIN: Final = "https://api.onesignal.com"
API_URL: Final = f"{API_ORIGIN}/notifications"
HTTP_TIMEOUT: Final = (3.0, 10.0)
# Час не переживает ночную паузу телефона: Android-браузер без фонового процесса
# показывает push только при следующем открытии. 12 часов переживают ночь и при
# этом не показывают уведомление на следующий день; само задание живёт сутки.
PUSH_TTL_SECONDS: Final = 43200
PROVIDER_STAT_KEYS: Final = (
    "successful",
    "failed",
    "errored",
    "converted",
    "remaining",
    "queued_at",
    "completed_at",
)


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
        logger.info(
            "OneSignal принял push",
            provider_id=str(payload["id"]),
            recipients=None,
            requested=len(subscription_ids),
        )
        return str(payload["id"])
    if recipients <= 0:
        raise PushRejectedError("push_no_recipients")
    logger.info(
        "OneSignal принял push",
        provider_id=str(payload["id"]),
        recipients=int(recipients),
        requested=len(subscription_ids),
    )
    return str(payload["id"])


def fetch_delivery_stats(provider_id: str) -> dict[str, object]:
    """Читает у провайдера счётчики доставки уже принятого сообщения.

    Приём сообщения ещё не означает показ на экране: ``successful`` показывает,
    сколько подписок реально получили push, ``failed`` и ``errored`` — отказы.

    Args:
        provider_id: ID сообщения из журнала доставки, не ключ идемпотентности.

    Returns:
        Безопасные счётчики и время обработки либо код ошибки запроса.
    """
    if not settings.ONESIGNAL_REST_API_KEY or not settings.ONESIGNAL_APP_ID:
        return {"error": "not_configured"}
    try:
        response = requests.get(
            f"{API_URL}/{provider_id}",
            params={"app_id": settings.ONESIGNAL_APP_ID},
            headers={"Authorization": f"Key {settings.ONESIGNAL_REST_API_KEY}"},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException:
        return {"error": "unavailable"}
    if response.status_code != 200:
        return {"error": f"http_{response.status_code}"}
    try:
        payload = response.json()
    except ValueError:
        return {"error": "invalid_response"}
    if not isinstance(payload, dict):
        return {"error": "invalid_response"}
    return {key: payload[key] for key in PROVIDER_STAT_KEYS if key in payload}


def delete_subscription(subscription_id: str) -> bool:
    """Удаляет подписку у провайдера, когда устройство отозвано в базе.

    Отзыв только локальной записи оставлял бы в OneSignal живой адрес, на
    который больше нельзя законно отправлять уведомления. Удаление необратимо,
    но не мешает браузеру подписаться заново и подтвердить устройство кодом.

    Args:
        subscription_id: ID подписки, сохранённый при подтверждении устройства.

    Returns:
        Истина, если подписка удалена или её уже не было у провайдера.
    """
    if not settings.ONESIGNAL_REST_API_KEY or not settings.ONESIGNAL_APP_ID:
        return False
    try:
        response = requests.delete(
            f"{API_ORIGIN}/apps/{settings.ONESIGNAL_APP_ID}"
            f"/subscriptions/{subscription_id}",
            headers={"Authorization": f"Key {settings.ONESIGNAL_REST_API_KEY}"},
            timeout=HTTP_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException:
        logger.warning(
            "Не удалось удалить подписку OneSignal: сеть",
            subscription_id=subscription_id,
        )
        return False
    # 404 означает, что подписки уже нет: цель достигнута без повтора.
    if response.status_code in (202, 404):
        logger.info(
            "Подписка OneSignal удалена",
            subscription_id=subscription_id,
            status=response.status_code,
        )
        return True
    logger.warning(
        "OneSignal отклонил удаление подписки",
        subscription_id=subscription_id,
        status=response.status_code,
    )
    return False
