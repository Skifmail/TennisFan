"""Telegram HTTP-транспорт с безопасными кодами ошибок и сведениями о повторах."""

import json
from typing import Any, BinaryIO

import requests

from .telegram_http import is_telegram_api_enabled, telegram_requests_proxies


class TelegramAPIError(RuntimeError):
    """Ошибка доставки без токена, URL запроса и персональных данных."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        retry_after: int | None = None,
        uncertain: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        self.uncertain = uncertain


def _retry_after(response: requests.Response, data: dict[str, Any]) -> int:
    """Прочитать паузу Telegram, не принимая некорректные или отрицательные значения."""
    parameters = data.get("parameters")
    raw = parameters.get("retry_after") if isinstance(parameters, dict) else None
    if raw is None:
        raw = response.headers.get("Retry-After", "60")
    try:
        seconds = int(raw)
    except (TypeError, ValueError, OverflowError):
        return 60
    return max(1, min(seconds, 7 * 24 * 60 * 60))


def telegram_call(
    token: str,
    method: str,
    payload: dict[str, Any],
    *,
    timeout: int = 10,
    photo: BinaryIO | None = None,
) -> Any:
    """Вызвать API; временный отказ отличить от неопределённого исхода отправки.

    Telegram не поддерживает ключ идемпотентности для sendMessage/sendPhoto.
    После потери ответа нельзя гарантировать, что сообщение не было принято.
    """
    if not token:
        raise TelegramAPIError("telegram_not_configured")
    if not is_telegram_api_enabled():
        raise TelegramAPIError("telegram_disabled")
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        if photo is None:
            response = requests.post(
                url,
                json=payload,
                timeout=(5, timeout),
                proxies=telegram_requests_proxies(),
            )
        else:
            form_payload = dict(payload)
            if isinstance(form_payload.get("reply_markup"), dict):
                form_payload["reply_markup"] = json.dumps(form_payload["reply_markup"])
            response = requests.post(
                url,
                data=form_payload,
                files={"photo": ("photo.jpg", photo)},
                timeout=(5, timeout),
                proxies=telegram_requests_proxies(),
            )
    except requests.ConnectTimeout:
        raise TelegramAPIError("telegram_connect_timeout", retryable=True) from None
    except requests.RequestException:
        # Не включаем исходное исключение: requests может содержать URL с токеном.
        raise TelegramAPIError("telegram_transport_uncertain", uncertain=True) from None

    try:
        raw_data = response.json()
    except ValueError:
        raw_data = None
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    if response.ok and data.get("ok") is True:
        return data.get("result")

    if data.get("ok") is not False:
        # Ошибка промежуточного шлюза не доказывает отказ Telegram в отправке.
        raise TelegramAPIError("telegram_protocol_error", uncertain=True)

    error_code = data.get("error_code")
    if not isinstance(error_code, int) or isinstance(error_code, bool):
        error_code = response.status_code
    if error_code == 429:
        raise TelegramAPIError(
            "telegram_rate_limited",
            retryable=True,
            retry_after=_retry_after(response, data),
        )
    if error_code >= 500:
        raise TelegramAPIError("telegram_server_error", retryable=True)
    if error_code == 403:
        raise TelegramAPIError("telegram_forbidden")
    if error_code == 401:
        raise TelegramAPIError("telegram_unauthorized")
    if error_code == 400:
        raise TelegramAPIError("telegram_bad_request")
    raise TelegramAPIError("telegram_rejected")
