"""Строгая проверка Telegram-update до чтения аккаунта и игровых действий."""

from __future__ import annotations

from typing import Any, TypeGuard, cast

MAX_ID = 2**63 - 1
UPDATE_TYPES = ("message", "callback_query", "chat_join_request")


def _integer(value: object, *, minimum: int = 0) -> TypeGuard[int]:
    """Не принимать bool, строку или число вне диапазона БД вместо Telegram ID."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= MAX_ID
    )


def _sender(value: object) -> dict[str, Any] | None:
    """Принимать только положительный ID человека из поля from, не user_chat_id."""
    if not isinstance(value, dict) or not _integer(value.get("id"), minimum=1):
        return None
    if value.get("is_bot", False) is not False:
        return None
    return cast(dict[str, Any], value)


def _private_message(value: object, *, sender_id: int) -> dict[str, Any] | None:
    """Личное сообщение обязано адресоваться тому же человеку, который его отправил."""
    if not isinstance(value, dict):
        return None
    chat = value.get("chat")
    if not isinstance(chat, dict):
        return None
    if (
        not _integer(chat.get("id"), minimum=1)
        or chat["id"] != sender_id
        or chat.get("type") != "private"
    ):
        return None
    if not _integer(value.get("message_id"), minimum=1) or not _integer(
        value.get("date")
    ):
        return None
    text = value.get("text", "")
    if not isinstance(text, str) or len(text) > 4096:
        return None
    return cast(dict[str, Any], value)


def validate_user_update(value: object) -> dict[str, Any] | None:
    """Пропускать только один поддерживаемый payload с корректной вложенной структурой."""
    if not isinstance(value, dict) or not _integer(value.get("update_id")):
        return None
    kinds = [kind for kind in UPDATE_TYPES if kind in value]
    if len(kinds) != 1:
        return None
    kind = kinds[0]
    payload = value[kind]
    if not isinstance(payload, dict):
        return None
    sender = _sender(payload.get("from"))
    if sender is None:
        return None
    sender_id = cast(int, sender["id"])
    if kind == "message":
        if _private_message(payload, sender_id=sender_id) is None:
            return None
    elif kind == "callback_query":
        if _private_message(payload.get("message"), sender_id=sender_id) is None:
            return None
        if not isinstance(payload.get("id"), str) or not 1 <= len(payload["id"]) <= 256:
            return None
        data = payload.get("data")
        if not isinstance(data, str):
            return None
        try:
            if not 1 <= len(data.encode("utf-8")) <= 64:
                return None
        except UnicodeError:
            return None
        # Никогда не доверять несуществующему в Bot API полю callback_data.
        payload = {**payload, "callback_data": data}
    else:
        chat = payload.get("chat")
        if not isinstance(chat, dict) or chat.get("type") not in {
            "group",
            "supergroup",
            "channel",
        }:
            return None
        chat_id = chat.get("id")
        if (
            not isinstance(chat_id, int)
            or isinstance(chat_id, bool)
            or not -MAX_ID <= chat_id < 0
        ):
            return None
        if not _integer(payload.get("date")) or not _integer(
            payload.get("user_chat_id"), minimum=1
        ):
            return None
    return {"update_id": value["update_id"], kind: payload}


def user_bot_update_namespace(token: str) -> str:
    """Сохранять дедупликацию при ротации токена, но отделять другой Telegram-бот."""
    bot_id, separator, _ = token.partition(":")
    if separator and bot_id.isascii() and bot_id.isdecimal() and 0 < len(bot_id) <= 19:
        return f"user:{bot_id}"
    return "user"
