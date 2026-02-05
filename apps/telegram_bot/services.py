"""
Сервис пользовательского Telegram-бота: отправка сообщений, получение username.
Привязка пользователя хранится в core.UserTelegramLink (общая таблица для всех ботов).
"""

import json
import logging
from typing import Tuple

import requests

from django.conf import settings

logger = logging.getLogger(__name__)


def _get_bot_token() -> str:
    """Токен бота для пользователей (уведомления, матчи, подписка)."""
    return (getattr(settings, "TELEGRAM_USER_BOT_TOKEN", None) or "").strip()


def is_configured() -> bool:
    """Проверка, что бот настроен."""
    return bool(_get_bot_token())


def get_bot_username() -> str | None:
    """
    Получить @username бота для ссылки привязки (t.me/BotUsername?start=TOKEN).
    """
    token = _get_bot_token()
    if not token:
        return None
    username = getattr(settings, "TELEGRAM_USER_BOT_USERNAME", None) or ""
    if username:
        return username.strip().lstrip("@")
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
        return (data.get("result") or {}).get("username")
    except Exception as e:
        logger.warning("Telegram user bot getMe failed: %s", e)
        return None


def send_message(
    chat_id: int | str,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
) -> Tuple[int | None, bool]:
    """
    Отправить сообщение пользователю от имени пользовательского бота.
    Возвращает (message_id, success).
    """
    token = _get_bot_token()
    if not token:
        logger.debug("TELEGRAM_USER_BOT_TOKEN not set")
        return None, False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        # Telegram API ожидает reply_markup как JSON-строку
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            err_body = r.text
            try:
                err_data = r.json()
                err_desc = err_data.get("description", err_body)
            except Exception:
                err_desc = err_body
            logger.warning(
                "Telegram user bot send_message failed: %s %s",
                r.status_code,
                err_desc,
            )
            return None, False
        data = r.json()
        result = data.get("result", {})
        return result.get("message_id"), True
    except Exception as e:
        logger.warning("Telegram user bot send_message failed: %s", e)
        return None, False


def send_to_user(chat_id: int, text: str, reply_markup: dict | None = None) -> bool:
    """Отправить сообщение пользователю по chat_id."""
    _, ok = send_message(chat_id, text, reply_markup=reply_markup)
    return ok
