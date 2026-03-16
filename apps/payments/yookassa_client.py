"""
Клиент для работы с платежами ЮKassa (YooKassa) по API v3.

Документация: https://yookassa.ru/developers/payment-acceptance/getting-started/quick-start
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

API_URL = "https://api.yookassa.ru/v3/payments"


def _create_auth(
    shop_id: str | None = None, secret_key: str | None = None
) -> tuple[str, str]:
    """Сформировать пару (shop_id, secret_key) для авторизации в ЮKassa.

    Args:
        shop_id (str | None): Явно переданный идентификатор магазина.
        secret_key (str | None): Явно переданный секретный ключ магазина.

    Returns:
        tuple[str, str]: Пара ``(shop_id, secret_key)``, очищенная от пробелов.

    Raises:
        ValueError: Если не удалось получить корректные учётные данные.
    """
    sid = (shop_id or getattr(settings, "YOOKASSA_SHOP_ID", "") or "").strip()
    skey = (secret_key or getattr(settings, "YOOKASSA_SECRET_KEY", "") or "").strip()
    if not sid or not skey:
        raise ValueError(
            "YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY должны быть заданы в настройках или переданы явно"
        )
    return sid, skey


def create_payment(
    amount: str,
    return_url: str,
    description: str,
    *,
    metadata: dict | None = None,
    customer_email: str | None = None,
    save_payment_method: bool | None = None,
) -> tuple[str, str]:
    """Создать платёж в ЮKassa и получить URL для редиректа пользователя.

    Функция поддерживает как обычные разовые платежи, так и платежи
    с возможностью сохранения способа оплаты для последующих автосписаний.

    Args:
        amount (str): Сумма в формате ``\"100.00\"`` (строка с двумя знаками после запятой).
        return_url (str): Абсолютный URL, на который пользователь вернётся после оплаты.
        description (str): Описание платежа (до 128 символов), отображается в ЛК и форме оплаты.
        metadata (dict | None): Произвольный словарь (тип, идентификаторы и т.п.) для
            восстановления контекста после возврата.
        customer_email (str | None): Email покупателя для чека 54-ФЗ (обязателен, если
            в ЛК ЮKassa включена передача чеков).
        save_payment_method (bool | None): Если ``True`` — запрашиваем сохранение способа
            оплаты (опциональное или обязательное в зависимости от настроек магазина).
            Если ``None`` — параметр не передаётся в ЮKassa.

    Returns:
        tuple[str, str]: Кортеж ``(payment_id, confirmation_url)``. Пользователя
        необходимо перенаправить на ``confirmation_url``.

    Raises:
        ValueError: Если не заданы ``YOOKASSA_SHOP_ID`` или ``YOOKASSA_SECRET_KEY``.
        RuntimeError: При ошибке ответа API (код не 200 или некорректный body).
    """
    shop_id, secret_key = _create_auth()

    payload: dict[str, Any] = {
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
    if save_payment_method is not None:
        payload["save_payment_method"] = bool(save_payment_method)

    # Чек по 54-ФЗ: обязателен, если в настройках магазина ЮKassa включена передача данных для чека.
    email = (customer_email or "").strip()
    if email and "@" in email:
        payload["receipt"] = {
            "customer": {"email": email[:64]},
            "items": [
                {
                    "description": (description or "Оплата")[:128],
                    "quantity": 1.0,
                    "amount": {"value": amount, "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }
            ],
            "internet": "true",
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

    data: dict[str, Any] = response.json()
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
    """Получить статус платежа по идентификатору.

    Args:
        payment_id (str): Идентификатор платежа, возвращённый ``create_payment`` или
            созданный при автосписании.

    Returns:
        str | None: Статус платежа (например, ``\"succeeded\"``, ``\"pending\"``,
        ``\"canceled\"``) или ``None`` при ошибке обращения к API.
    """
    try:
        shop_id, secret_key = _create_auth()
    except ValueError:
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
    except Exception as exc:
        logger.warning("YooKassa get payment failed: %s", exc)
        return None


def get_payment_details(payment_id: str) -> dict[str, Any] | None:
    """Получить полный объект платежа из ЮKassa.

    Используется, в частности, для извлечения информации о сохранённом
    способе оплаты после успешного платежа.

    Args:
        payment_id (str): Идентификатор платежа из ЮKassa.

    Returns:
        dict[str, Any] | None: Словарь с данными платежа (как возвращает API
        ЮKassa) или ``None`` при ошибке запроса.
    """
    try:
        shop_id, secret_key = _create_auth()
    except ValueError:
        return None

    url = f"{API_URL}/{payment_id}"
    try:
        response = requests.get(
            url,
            auth=(shop_id, secret_key),
            timeout=10,
        )
        if response.status_code != 200:
            logger.warning(
                "YooKassa get_payment_details failed: status=%s body=%s",
                response.status_code,
                response.text[:500],
            )
            return None
        data: dict[str, Any] = response.json()
        return data
    except Exception as exc:
        logger.warning("YooKassa get_payment_details exception: %s", exc)
        return None


def create_recurring_payment(
    amount: str,
    description: str,
    payment_method_id: str,
    *,
    metadata: dict | None = None,
) -> tuple[str, str]:
    """Создать автоплатёж по сохранённому способу оплаты (без участия пользователя).

    Args:
        amount (str): Сумма в формате ``\"100.00\"`` (строка с двумя знаками после запятой).
        description (str): Краткое описание автоплатежа для ЛК и выписок.
        payment_method_id (str): Идентификатор сохранённого способа оплаты
            из ЮKassa (поле ``payment_method.id``).
        metadata (dict | None): Дополнительные данные для сопоставления платежа
            с объектами в нашей системе.

    Returns:
        Tuple[str, str]: Кортеж ``(payment_id, status)``, где ``status`` — статус
        платежа, возвращённый ЮKassa (например, ``\"succeeded\"`` или ``\"pending\"``).

    Raises:
        ValueError: Если не заданы ``YOOKASSA_SHOP_ID`` или ``YOOKASSA_SECRET_KEY``.
        RuntimeError: При ошибке ответа API (код не 200).
    """
    shop_id, secret_key = _create_auth()

    payload: dict[str, Any] = {
        "amount": {"value": amount, "currency": "RUB"},
        "capture": True,
        "payment_method_id": payment_method_id,
        "description": (description or "Автоплатёж")[:128],
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
            "YooKassa create recurring payment failed: status=%s body=%s",
            response.status_code,
            response.text[:500],
        )
        raise RuntimeError(
            f"ЮKassa вернула ошибку при автоплатеже: {response.status_code}."
        )

    data: dict[str, Any] = response.json()
    payment_id = data.get("id")
    status = data.get("status")

    if not isinstance(payment_id, str) or not isinstance(status, str):
        logger.warning("YooKassa recurring response missing id or status: %s", data)
        raise RuntimeError("Неверный ответ ЮKassa при автоплатеже.")

    return payment_id, status


def create_payment_with_credentials(
    shop_id: str,
    secret_key: str,
    amount: str,
    return_url: str,
    description: str,
    *,
    metadata: dict | None = None,
    customer_email: str | None = None,
) -> tuple[str, str]:
    """Создать платёж в ЮKassa с явными учётными данными магазина клуба.

    Args:
        shop_id (str): Идентификатор магазина ЮKassa (клуба).
        secret_key (str): Секретный ключ API для магазина клуба.
        amount (str): Сумма платежа в формате ``\"100.00\"``.
        return_url (str): URL возврата игрока после оплаты.
        description (str): Описание платежа.
        metadata (dict | None): Метаданные для связывания платежа с объектами клуба.
        customer_email (str | None): Email игрока для чека (если требуется).

    Returns:
        tuple[str, str]: Кортеж ``(payment_id, confirmation_url)``.
    """
    sid, skey = _create_auth(shop_id=shop_id, secret_key=secret_key)
    payload: dict[str, Any] = {
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
    email = (customer_email or "").strip()
    if email and "@" in email:
        payload["receipt"] = {
            "customer": {"email": email[:64]},
            "items": [
                {
                    "description": (description or "Оплата")[:128],
                    "quantity": 1.0,
                    "amount": {"value": amount, "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }
            ],
            "internet": "true",
        }

    idempotence_key = str(uuid.uuid4())
    response = requests.post(
        API_URL,
        auth=(sid, skey),
        headers={
            "Idempotence-Key": idempotence_key,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )

    if response.status_code != 200:
        logger.warning(
            "YooKassa create payment (club credentials) failed: status=%s body=%s",
            response.status_code,
            response.text[:500],
        )
        raise RuntimeError(
            f"ЮKassa вернула ошибку: {response.status_code}. Проверьте Shop ID и Secret Key клуба."
        )

    data: dict[str, Any] = response.json()
    payment_id = data.get("id")
    confirmation_url = (
        (data.get("confirmation") or {}).get("confirmation_url")
        if isinstance(data.get("confirmation"), dict)
        else None
    )
    if not payment_id or not confirmation_url:
        logger.warning(
            "YooKassa response (club credentials) missing id or confirmation_url: %s",
            data,
        )
        raise RuntimeError("Неверный ответ ЮKassa: нет ссылки на оплату.")
    return payment_id, confirmation_url


def get_payment_status_with_credentials(
    payment_id: str,
    shop_id: str,
    secret_key: str,
) -> str | None:
    """Получить статус платежа с использованием учётных данных магазина клуба.

    Args:
        payment_id (str): Идентификатор платежа ЮKassa.
        shop_id (str): Идентификатор магазина клуба.
        secret_key (str): Секретный ключ магазина клуба.

    Returns:
        str | None: Статус платежа или ``None`` при ошибке.
    """
    try:
        sid, skey = _create_auth(shop_id=shop_id, secret_key=secret_key)
    except ValueError:
        return None
    url = f"{API_URL}/{payment_id}"
    try:
        response = requests.get(
            url,
            auth=(sid, skey),
            timeout=10,
        )
        if response.status_code != 200:
            return None
        status = response.json().get("status")
        return status if isinstance(status, str) else None
    except Exception as exc:
        logger.warning("YooKassa get payment with club credentials failed: %s", exc)
        return None
