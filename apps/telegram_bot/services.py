"""
Сервис пользовательского Telegram-бота: отправка сообщений, получение username.
Привязка пользователя хранится в core.UserTelegramLink (общая таблица для всех ботов).
"""

import json
import logging
import time
from typing import Any, cast

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_bot_token() -> str:
    """Токен бота для пользователей (уведомления, матчи, подписка)."""
    return (getattr(settings, "TELEGRAM_USER_BOT_TOKEN", None) or "").strip()


def _get_private_chat_id() -> int | None:
    """ID закрытого сообщества Telegram (супергруппа), если настроен."""
    raw = (getattr(settings, "TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID", None) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID: %s", raw)
        return None


def is_configured() -> bool:
    """Проверка, что бот настроен."""
    return bool(_get_bot_token())


def is_private_chat_configured() -> bool:
    """Проверка, что настроен ID приватного чата сообщества."""
    return _get_private_chat_id() is not None


def _api_post(
    method: str, payload: dict[str, Any], timeout: int = 10
) -> tuple[Any, bool]:
    """Унифицированный POST в Telegram Bot API с проверкой `ok` в JSON-ответе."""
    token = _get_bot_token()
    if not token:
        return None, False
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        if not r.ok:
            logger.warning(
                "Telegram API %s failed: %s %s", method, r.status_code, r.text[:300]
            )
            return None, False
        data = r.json()
        if not data.get("ok", False):
            logger.warning(
                "Telegram API %s returned ok=false: %s",
                method,
                data.get("description", "unknown error"),
            )
            return None, False
        return data.get("result"), True
    except Exception as exc:
        logger.warning("Telegram API %s exception: %s", method, exc)
        return None, False


def create_private_chat_invite_link(
    expire_seconds: int = 1800, member_limit: int = 1
) -> str | None:
    """
    Создать одноразовую ссылку-приглашение в закрытый чат сообщества.
    По умолчанию действует 30 минут и на 1 участника.
    """
    chat_id = _get_private_chat_id()
    if chat_id is None:
        logger.warning(
            "create_private_chat_invite_link: private chat is not configured"
        )
        return None

    expire_date = int(time.time()) + max(60, int(expire_seconds))
    payload = {
        "chat_id": chat_id,
        "expire_date": expire_date,
        "member_limit": max(1, int(member_limit)),
        "creates_join_request": False,
    }
    result, ok = _api_post("createChatInviteLink", payload, timeout=10)
    if not ok or not isinstance(result, dict):
        return None
    return result.get("invite_link")


def get_private_chat_member_status(user_chat_id: int) -> str | None:
    """Статус участника в закрытом чате (member/admin/left/kicked и т.д.)."""
    chat_id = _get_private_chat_id()
    if chat_id is None:
        return None
    result, ok = _api_post(
        "getChatMember",
        {"chat_id": chat_id, "user_id": int(user_chat_id)},
        timeout=10,
    )
    if not ok or not isinstance(result, dict):
        return None
    return result.get("status")


def kick_from_private_chat(user_chat_id: int) -> bool:
    """
    Удалить пользователя из закрытого чата.
    Используем ban + unban, чтобы пользователь мог вернуться после продления.
    """
    chat_id = _get_private_chat_id()
    if chat_id is None:
        return False

    # Сначала баним, затем сразу снимаем бан.
    _, ban_ok = _api_post(
        "banChatMember",
        {"chat_id": chat_id, "user_id": int(user_chat_id), "revoke_messages": False},
        timeout=10,
    )
    if not ban_ok:
        return False
    _, unban_ok = _api_post(
        "unbanChatMember",
        {"chat_id": chat_id, "user_id": int(user_chat_id), "only_if_banned": True},
        timeout=10,
    )
    return unban_ok


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
) -> tuple[int | None, bool]:
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


def edit_message_text(
    chat_id: int | str,
    message_id: int,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
) -> bool:
    """Редактировать текст и/или кнопки сообщения."""
    token = _get_bot_token()
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            logger.warning("editMessageText failed: %s %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        logger.warning("editMessageText failed: %s", e)
        return False


def send_photo(
    chat_id: int | str,
    photo: str | bytes,
    caption: str | None = None,
    parse_mode: str = "HTML",
) -> tuple[int | None, bool]:
    """
    Отправить фото пользователю от имени пользовательского бота.
    photo: путь к файлу (str), HTTP(S) URL (str) или bytes.
    Возвращает (message_id, success).
    """
    token = _get_bot_token()
    if not token:
        logger.debug("TELEGRAM_USER_BOT_TOKEN not set")
        return None, False
    api_url = f"https://api.telegram.org/bot{token}/sendPhoto"
    payload: dict = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = parse_mode
    files: dict[str, object] | None = None
    if isinstance(photo, str) and (
        photo.startswith("http://") or photo.startswith("https://")
    ):
        payload["photo"] = photo
    elif isinstance(photo, str):
        try:
            files = {"photo": open(photo, "rb")}
        except FileNotFoundError:
            logger.warning("send_photo: file not found %s", photo)
            return None, False
    elif isinstance(photo, bytes):
        from io import BytesIO

        files = {"photo": BytesIO(photo)}
    else:
        logger.warning("send_photo: unsupported photo type %s", type(photo))
        return None, False
    try:
        if files:
            r = requests.post(api_url, data=payload, files=cast(Any, files), timeout=30)
        else:
            r = requests.post(api_url, json=payload, timeout=10)
        if not r.ok:
            logger.warning("send_photo failed: %s %s", r.status_code, r.text[:200])
            return None, False
        data = r.json()
        result = data.get("result", {})
        return result.get("message_id"), True
    except Exception as e:
        logger.warning("send_photo exception: %s", e)
        return None, False
    finally:
        if files:
            for f in files.values():
                if hasattr(f, "close"):
                    f.close()
