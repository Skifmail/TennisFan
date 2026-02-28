"""
Сервис обратной связи через Telegram Bot API.
Отправка сообщений админу и пользователю; только webhook, без polling.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Первое приветствие админу в боте поддержки (при /start)
ADMIN_GREETING_SUPPORT = (
    "👋 <b>Бот поддержки TennisFan</b>\n\n"
    "Сюда приходят обращения с сайта. Чтобы ответить пользователю, выберите его сообщение (Reply) и напишите ответ."
)

# Приветствие для пользователя (не админа), когда пишет в бот без привязки
USER_GREETING_SUPPORT = (
    "Здравствуйте!\n\n"
    "Это бот обратной связи TennisFan. Чтобы мы могли ответить вам здесь, отправьте обращение через форму на сайте и перейдите по ссылке из уведомления — тогда ответ придёт в этот чат."
)


def _get_bot_token() -> str:
    """Токен бота поддержки (отдельный бот для обратной связи)."""
    return (getattr(settings, "TELEGRAM_SUPPORT_BOT_TOKEN", None) or "").strip()


def _get_admin_chat_ids() -> list[str]:
    """Список chat_id администраторов (из TELEGRAM_ADMIN_CHAT_IDS или одного TELEGRAM_ADMIN_CHAT_ID)."""
    ids = getattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", None)
    if ids:
        return [str(cid).strip() for cid in ids if str(cid).strip()]
    single = (getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", None) or "").strip()
    return [single] if single else []


def is_telegram_configured() -> bool:
    """Проверка, что бот и хотя бы один админский chat_id заданы."""
    return bool(_get_bot_token() and _get_admin_chat_ids())


def get_admin_chat_id_value() -> str | None:
    """Первый TELEGRAM_ADMIN_CHAT_ID для обратной совместимости (например whitelist)."""
    ids = _get_admin_chat_ids()
    return ids[0] if ids else None


def get_admin_chat_ids() -> list[str]:
    """Список всех chat_id, которым шлём уведомления и сообщения поддержки."""
    return _get_admin_chat_ids()


def send_message(
    chat_id: int | str,
    text: str,
    parse_mode: str = "HTML",
) -> tuple[int | None, bool]:
    """
    Отправить сообщение в Telegram в указанный chat_id.
    Возвращает (message_id из ответа API, success).
    """
    token = _get_bot_token()
    if not token:
        logger.debug("Telegram support: TELEGRAM_SUPPORT_BOT_TOKEN not set")
        return None, False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        result = data.get("result", {})
        msg_id = result.get("message_id")
        return msg_id, True
    except Exception as e:
        logger.warning("Telegram send_message failed: %s", e)
        return None, False


def send_to_admin(text: str) -> tuple[int | None, bool]:
    """Отправить сообщение всем администраторам. Возвращает (message_id последней отправки, True если хотя бы одна успешна)."""
    chat_ids = _get_admin_chat_ids()
    if not chat_ids:
        return None, False
    last_msg_id = None
    any_ok = False
    for chat_id in chat_ids:
        msg_id, ok = send_message(chat_id, text)
        if ok:
            last_msg_id = msg_id
            any_ok = True
    return last_msg_id, any_ok


def edit_message(
    chat_id: int | str,
    message_id: int,
    text: str,
    parse_mode: str = "HTML",
) -> bool:
    """Отредактировать сообщение бота (например, добавить пометку «Ответ отправлен»)."""
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
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.warning("Telegram edit_message failed: %s", e)
        return False


def send_to_user(telegram_chat_id: int, text: str) -> bool:
    """Отправить сообщение пользователю в личный чат. Telegram не даёт писать первым — только после /start."""
    _, ok = send_message(telegram_chat_id, text)
    return ok


def _escape(s: str) -> str:
    if not s:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_support_message_to_admin(
    support_message_id: int,
    user_display: str,
    user_email: str,
    subject: str,
    text: str,
    source: str = "сайт",
    is_guest: bool = False,
    guest_contact: str = "",
    guest_telegram_username: str = "",
) -> str:
    """
    Форматирует текст сообщения для админа с идентификатором SupportMessage#id,
    чтобы по reply_to_message.message_id найти запись в БД.
    """
    subj = _escape(subject or "—")
    msg = _escape(text or "")
    user_d = _escape(user_display or "—")
    email = _escape(user_email or "—")
    contact = _escape(guest_contact or "—")

    if is_guest:
        tg_info = ""
        if guest_telegram_username:
            tg_info = f"\nTelegram: @{_escape(guest_telegram_username)}\n"
            tg_info += "<i>Пользователь может привязать бота по ссылке из ответа на сайте. После привязки вы сможете ответить через Telegram.</i>\n"
        return (
            f"📩 <b>Обратная связь #{support_message_id}</b> ({source})\n\n"
            f"⚠️ <b>Незарегистрированный пользователь</b>\n"
            f"От: {user_d}\n"
            f"Контакт: {contact}{tg_info}"
            f"Тема: {subj}\n\n"
            f"Сообщение:\n{msg}\n\n"
            "<i>Ответьте на это сообщение — ответ будет сохранён. Если пользователь привязал бота, ответ уйдёт в Telegram, иначе свяжитесь по указанным контактам.</i>"
        )
    else:
        return (
            f"📩 <b>Обратная связь #{support_message_id}</b> ({source})\n\n"
            f"От: {user_d}\n"
            f"Email: {email}\n"
            f"Тема: {subj}\n\n"
            f"Сообщение:\n{msg}\n\n"
            "<i>Ответьте на это сообщение — ответ уйдёт пользователю в Telegram.</i>"
        )


def get_bot_username() -> str | None:
    """
    Получить @username бота поддержки для ссылки привязки (t.me/BotUsername?start=TOKEN).
    """
    token = _get_bot_token()
    if not token:
        return None
    username = getattr(settings, "TELEGRAM_SUPPORT_BOT_USERNAME", None) or ""
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
        logger.warning("getMe failed: %s", e)
        return None
