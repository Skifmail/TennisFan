"""
Клиент для создания платежей ЮKassa (YooKassa) по API v3.

Документация: https://yookassa.ru/developers/payment-acceptance/getting-started/quick-start
"""

import logging
import uuid

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.yookassa.ru/v3/payments"


def create_payment(
    amount: str,
    return_url: str,
    description: str,
    *,
    metadata: dict | None = None,
) -> tuple[str, str]:
    """
    Создать платёж в ЮKassa и получить URL для редиректа пользователя.

    Args:
        amount: Сумма в формате "100.00" (строка с двумя знаками после запятой).
        return_url: URL, на который пользователь вернётся после оплаты.
        description: Описание платежа (до 128 символов), видно в ЛК и при оплате.
        metadata: Произвольный словарь (type, item_id, next и т.д.) для восстановления контекста после возврата.

    Returns:
        Кортеж (payment_id, confirmation_url). Редирект пользователя на confirmation_url.

    Raises:
        ValueError: Если не заданы shop_id или secret_key.
        RuntimeError: При ошибке ответа API (не 200 или ошибка в body).
    """
    shop_id = (getattr(settings, "YOOKASSA_SHOP_ID", None) or "").strip()
    secret_key = (getattr(settings, "YOOKASSA_SECRET_KEY", None) or "").strip()
    if not shop_id or not secret_key:
        raise ValueError(
            "YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY должны быть заданы в настройках"
        )

    payload = {
        "amount": {"value": amount, "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": (description or "Оплата")[:128],
    }
    if metadata:
        payload["metadata"] = {
            str(k): str(v)
            for k, v in metadata.items()
            if v is not None and str(v).strip()
        }

    idempotence_key = str(uuid.uuid4())
    response = requests.post(
        API_URL,
        auth=(shop_id, secret_key),
        headers={
            "Idempotence-Key": idempotence_key,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )

    if response.status_code != 200:
        logger.warning(
            "YooKassa create payment failed: status=%s body=%s",
            response.status_code,
            response.text[:500],
        )
        raise RuntimeError(
            f"ЮKassa вернула ошибку: {response.status_code}. Проверьте настройки и сумму."
        )

    data = response.json()
    payment_id = data.get("id")
    confirmation_url = (
        (data.get("confirmation") or {}).get("confirmation_url")
        if isinstance(data.get("confirmation"), dict)
        else None
    )

    if not payment_id or not confirmation_url:
        logger.warning("YooKassa response missing id or confirmation_url: %s", data)
        raise RuntimeError("Неверный ответ ЮKassa: нет ссылки на оплату.")

    return payment_id, confirmation_url


def get_payment_status(payment_id: str) -> str | None:
    """
    Получить статус платежа по ID.

    Args:
        payment_id: Идентификатор платежа из create_payment.

    Returns:
        Статус: "succeeded", "pending", "canceled" и т.д. или None при ошибке.
    """
    shop_id = (getattr(settings, "YOOKASSA_SHOP_ID", None) or "").strip()
    secret_key = (getattr(settings, "YOOKASSA_SECRET_KEY", None) or "").strip()
    if not shop_id or not secret_key:
        return None

    url = f"{API_URL}/{payment_id}"
    try:
        response = requests.get(
            url,
            auth=(shop_id, secret_key),
            timeout=10,
        )
        if response.status_code != 200:
            return None
        status = response.json().get("status")
        return status if isinstance(status, str) else None
    except Exception as e:
        logger.warning("YooKassa get payment failed: %s", e)
        return None
