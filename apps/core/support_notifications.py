"""Email-уведомления для диалогов поддержки."""

from __future__ import annotations

import logging
from typing import cast

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from .models import SupportMessage

logger = logging.getLogger(__name__)
DEFAULT_SUPPORT_EMAIL_TIMEOUT_SECONDS = 3


def _base_url() -> str:
    """Вернуть базовый URL платформы.

    Returns:
        str: Базовый URL без завершающего слеша.
    """
    raw = (getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", "") or "").strip()
    if raw:
        return raw.rstrip("/")
    domain = getattr(settings, "SITE_DOMAIN", "tennisfan.ru")
    return f"https://{domain}"


def _support_email_timeout_seconds() -> int:
    """Вернуть timeout отправки email для поддержки.

    Returns:
        int: Таймаут в секундах для SMTP-соединения.
    """
    raw_value = getattr(
        settings,
        "SUPPORT_EMAIL_TIMEOUT_SECONDS",
        DEFAULT_SUPPORT_EMAIL_TIMEOUT_SECONDS,
    )
    try:
        timeout_seconds = int(raw_value)
    except (TypeError, ValueError):
        timeout_seconds = DEFAULT_SUPPORT_EMAIL_TIMEOUT_SECONDS
    return max(timeout_seconds, 1)


def _send_with_timeout(message: EmailMultiAlternatives) -> int:
    """Отправить письмо с ограниченным timeout.

    Args:
        message (EmailMultiAlternatives): Подготовленное письмо для отправки.

    Returns:
        int: Количество отправленных сообщений.
    """
    connection = get_connection(
        fail_silently=False,
        timeout=_support_email_timeout_seconds(),
    )
    message.connection = connection
    return cast(int, message.send(fail_silently=False))


def send_admin_support_notification(message: SupportMessage) -> bool:
    """Отправить письмо администратору о новом обращении.

    Args:
        message (SupportMessage): Созданное сообщение пользователя.

    Returns:
        bool: True, если письмо отправлено успешно, иначе False.
    """
    admin_email = (getattr(settings, "ADMIN_NOTIFICATIONS_EMAIL", "") or "").strip()
    if not admin_email:
        return False

    thread = message.thread
    author_name = (
        message.user.get_full_name() if message.user else message.guest_name or "Гость"
    )
    author_email = message.user.email if message.user else thread.guest_email
    context = {
        "message": message,
        "thread": thread,
        "author_name": author_name,
        "author_email": author_email,
        "thread_url": f"{_base_url()}/platform/support/{thread.id}/",
    }
    subject_tail = (message.subject or message.text[:60]).strip() or "Новое сообщение"
    subject = f"[TennisFan Support #{thread.id}] {subject_tail}"
    html_content = render_to_string("emails/support_admin_new_message.html", context)
    text_content = strip_tags(html_content)
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tennisfan.ru"),
        to=[admin_email],
    )
    msg.attach_alternative(html_content, "text/html")
    try:
        _send_with_timeout(msg)
        return True
    except Exception:
        logger.exception("send_admin_support_notification failed")
        return False


def send_user_support_reply(message: SupportMessage) -> bool:
    """Отправить пользователю email с ответом администратора.

    Args:
        message (SupportMessage): Сообщение администратора.

    Returns:
        bool: True, если письмо отправлено успешно, иначе False.
    """
    thread = message.thread
    user_email = ""
    if thread.user and thread.user.email:
        user_email = thread.user.email
    elif thread.guest_email:
        user_email = thread.guest_email
    if not user_email:
        return False

    context = {
        "message": message,
        "thread": thread,
        "reply_url": f"{_base_url()}/?open_support=1",
    }
    subject = f"Ответ поддержки TennisFan по обращению #{thread.id}"
    html_content = render_to_string("emails/support_user_reply.html", context)
    text_content = strip_tags(html_content)
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tennisfan.ru"),
        to=[user_email],
    )
    msg.attach_alternative(html_content, "text/html")
    try:
        _send_with_timeout(msg)
        return True
    except Exception:
        logger.exception("send_user_support_reply failed")
        return False
