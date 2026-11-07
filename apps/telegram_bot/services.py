"""
Сервис пользовательского Telegram-бота: отправка сообщений, получение username.
Привязка пользователя хранится в core.UserTelegramLink (общая таблица для всех ботов).
"""

import time
from contextlib import ExitStack
from io import BytesIO
from typing import Any, BinaryIO, cast

import requests
from django.conf import settings
from loguru import logger

from apps.telegram_bot.telegram_http import (
    is_telegram_api_enabled,
    telegram_requests_proxies,
)

from .transport import TelegramAPIError, telegram_call


def _get_bot_token() -> str:
    """Токен бота для пользователей (уведомления, матчи, подписка)."""
    return (getattr(settings, "TELEGRAM_USER_BOT_TOKEN", None) or "").strip()


def _configured_group_id(name: str) -> int | None:
    """Прочитать только явно настроенный числовой ID группы или канала."""
    raw = getattr(settings, name, "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value < 0 else None


def _get_private_chat_id() -> int | None:
    """Единственный ID закрытого сообщества для входа и удаления участников."""
    return _configured_group_id("TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID")


def _get_private_chat_channel_id() -> int | None:
    """Совместимый псевдоним единственного ID; запасных каналов нет."""
    return _get_private_chat_id()


def is_configured() -> bool:
    """Проверка, что бот настроен и исходящие вызовы к API включены."""
    return bool(_get_bot_token()) and is_telegram_api_enabled()


def is_private_chat_configured() -> bool:
    """Закрытый чат включён явно и не совпадает с открытым сообществом."""
    enabled = getattr(settings, "TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED", False)
    if str(enabled).strip().lower() not in {"true", "1", "yes", "on"}:
        return False
    private_id = _get_private_chat_id()
    public_id = _configured_group_id("TELEGRAM_PUBLIC_COMMUNITY_CHAT_ID")
    return private_id is not None and public_id is not None and private_id != public_id


def _api_post(
    method: str, payload: dict[str, Any], timeout: int = 10
) -> tuple[Any, bool]:
    """Унифицированный POST в Telegram Bot API с проверкой `ok` в JSON-ответе."""
    try:
        return telegram_call(_get_bot_token(), method, payload, timeout=timeout), True
    except TelegramAPIError as exc:
        logger.warning("Telegram API отказ: method={}, code={}", method, exc.code)
        return None, False


def create_private_chat_invite_link(
    expire_seconds: int = 1800, member_limit: int = 1, chat_id: int | None = None
) -> str | None:
    """Создать короткую ссылку с заявкой на вступление, а не свободным входом.

    Право проверяется повторно при chat_join_request. Аргумент member_limit
    сохранён для совместимости: Telegram не сочетает его с заявкой на вступление.
    """
    configured_id = _get_private_chat_id()
    if not is_private_chat_configured() or (
        chat_id is not None and chat_id != configured_id
    ):
        return None
    expire_date = int(time.time()) + max(60, int(expire_seconds))
    payload = {
        "chat_id": configured_id,
        "expire_date": expire_date,
        "creates_join_request": True,
    }
    result, ok = _api_post("createChatInviteLink", payload, timeout=10)
    if not ok or not isinstance(result, dict):
        return None
    invite_link = result.get("invite_link")
    return invite_link if isinstance(invite_link, str) and invite_link else None


def get_chat_id_from_invite_link(invite_link: str) -> int | None:
    """
    Получить chat_id канала/чата из invite link через Telegram Bot API.
    Использует метод getChat с username или проверяет через joinChat.
    """
    # Из invite link вида https://t.me/+VPczFLt3fZk5MTUy нельзя напрямую получить chat_id
    # Нужно использовать другой подход - получить через настройки или использовать
    # метод getChatMember с user_id бота
    # Для упрощения, можно использовать настройку TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID
    # или добавить отдельную настройку для этого канала
    return None


def get_private_chat_member_status(user_chat_id: int) -> str | None:
    """Статус участника в закрытом чате (member/admin/left/kicked и т.д.)."""
    chat_id = _get_private_chat_id()
    if not is_private_chat_configured() or chat_id is None:
        return None
    result, ok = _api_post(
        "getChatMember",
        {"chat_id": chat_id, "user_id": int(user_chat_id)},
        timeout=10,
    )
    if not ok or not isinstance(result, dict):
        return None
    status = result.get("status")
    return status if isinstance(status, str) else None


def kick_from_private_chat(user_chat_id: int) -> bool:
    """
    Удалить пользователя из закрытого чата.
    Используем ban + unban, чтобы пользователь мог вернуться после продления.
    """
    chat_id = _get_private_chat_id()
    if not is_private_chat_configured() or chat_id is None:
        return False

    # Сначала баним, затем сразу снимаем бан.
    ban_result, ban_ok = _api_post(
        "banChatMember",
        {"chat_id": chat_id, "user_id": int(user_chat_id), "revoke_messages": False},
        timeout=10,
    )
    if not ban_ok or ban_result is not True:
        return False
    unban_result, unban_ok = _api_post(
        "unbanChatMember",
        {"chat_id": chat_id, "user_id": int(user_chat_id), "only_if_banned": True},
        timeout=10,
    )
    return unban_ok and unban_result is True


def resolve_private_chat_join_request(user_chat_id: int, *, approve: bool) -> bool:
    """Одобрить или отклонить заявку только в явно включённый закрытый чат."""
    chat_id = _get_private_chat_id()
    if not is_private_chat_configured() or chat_id is None or user_chat_id <= 0:
        return False
    method = "approveChatJoinRequest" if approve else "declineChatJoinRequest"
    result, ok = _api_post(method, {"chat_id": chat_id, "user_id": user_chat_id})
    return ok and result is True


def get_bot_username() -> str | None:
    """
    Получить @username бота для ссылки привязки (t.me/BotUsername?start=TOKEN).
    """
    username = getattr(settings, "TELEGRAM_USER_BOT_USERNAME", None) or ""
    if username:
        return username.strip().lstrip("@")
    if not is_telegram_api_enabled():
        return None
    token = _get_bot_token()
    if not token:
        return None
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=5,
            proxies=telegram_requests_proxies(),
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict) or data.get("ok") is not True:
            return None
        result = data.get("result")
        username = result.get("username") if isinstance(result, dict) else None
        return username if isinstance(username, str) else None
    except requests.RequestException as exc:
        # Не логируем str(exc): в нём может быть URL с токеном бота. Повод — сеть/блокировка, не ERROR.
        logger.debug(
            "Telegram getMe: сеть ({}), username не получен",
            type(exc).__name__,
        )
        return None
    except Exception as exc:
        logger.debug(
            "Telegram getMe: неожиданная ошибка ({})",
            type(exc).__name__,
        )
        return None


def send_message(
    chat_id: int | str,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: dict[str, Any] | None = None,
) -> tuple[int | None, bool]:
    """
    Отправить сообщение пользователю от имени пользовательского бота.
    Возвращает (message_id, success).
    """
    try:
        return send_message_or_raise(chat_id, text, parse_mode, reply_markup), True
    except TelegramAPIError as exc:
        logger.warning("Telegram sendMessage отказ: code={}", exc.code)
        return None, False


def _message_id(result: Any) -> int:
    """Принятие сообщения подтверждается только корректным message_id."""
    value = result.get("message_id") if isinstance(result, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise TelegramAPIError("telegram_protocol_error", uncertain=True)
    return value


def send_message_or_raise(
    chat_id: int | str,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: dict[str, Any] | None = None,
) -> int:
    """Отправить сообщение; передать worker безопасный тип отказа и retry_after."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _message_id(telegram_call(_get_bot_token(), "sendMessage", payload))


def send_to_user(
    chat_id: int, text: str, reply_markup: dict[str, Any] | None = None
) -> bool:
    """Отправить сообщение пользователю по chat_id."""
    _, ok = send_message(chat_id, text, reply_markup=reply_markup)
    return ok


def edit_message_text(
    chat_id: int | str,
    message_id: int,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """Редактировать текст и/или кнопки сообщения."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _, ok = _api_post("editMessageText", payload)
    return ok


def send_photo(
    chat_id: int | str,
    photo: str | bytes,
    caption: str | None = None,
    parse_mode: str = "HTML",
    reply_markup: dict[str, Any] | None = None,
) -> tuple[int | None, bool]:
    """
    Отправить фото пользователю от имени пользовательского бота.
    photo: путь к файлу (str), HTTP(S) URL (str) или bytes.
    Возвращает (message_id, success).
    """
    try:
        return (
            send_photo_or_raise(chat_id, photo, caption, parse_mode, reply_markup),
            True,
        )
    except TelegramAPIError as exc:
        logger.warning("Telegram sendPhoto отказ: code={}", exc.code)
        return None, False


def send_photo_or_raise(
    chat_id: int | str,
    photo: str | bytes,
    caption: str | None = None,
    parse_mode: str = "HTML",
    reply_markup: dict[str, Any] | None = None,
) -> int:
    """Отправить фото с проверенным ответом; закрыть локальный файл при любом исходе."""
    payload: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        with ExitStack() as stack:
            stream: BinaryIO | None = None
            if isinstance(photo, bytes):
                stream = stack.enter_context(BytesIO(photo))
            elif photo.startswith(("http://", "https://")):
                payload["photo"] = photo
            else:
                stream = cast(BinaryIO, stack.enter_context(open(photo, "rb")))
            return _message_id(
                telegram_call(
                    _get_bot_token(), "sendPhoto", payload, timeout=30, photo=stream
                )
            )
    except OSError:
        raise TelegramAPIError("telegram_photo_unavailable") from None
