"""Уведомления поддержки в ЛК, push и по электронной почте."""

from __future__ import annotations

import logging
import threading
from typing import cast

from django.conf import settings
from django.core.mail import get_connection
from django.db import connection, transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from apps.notifications.infrastructure.email import EventEmail as EmailMultiAlternatives
from apps.notifications.services import create_notification
from apps.users.models import User

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


@transaction.atomic
def _notify_support_admins(message: SupportMessage) -> str:
    """Создаёт ЛК и push всем активным сотрудникам, независимо от почтового адресата."""
    report = getattr(message, "bug_report", None)
    event_key = "support.bug_report" if report else "support.admin_message"
    text = (
        f"Новое сообщение об ошибке №{report.pk}. Откройте диалог поддержки."
        if report
        else f"Новое сообщение в диалоге поддержки №{message.thread_id}."
    )
    url = f"/platform/support/{message.thread_id}/"
    for admin in User.objects.filter(is_active=True).filter(
        Q(is_staff=True) | Q(is_superuser=True)
    ):
        create_notification(event_key=event_key, user=admin, message=text, url=url)
    return event_key


def send_admin_support_notification(message: SupportMessage) -> bool:
    """Уведомить всех сотрудников через центр и отправить письмо на адрес поддержки.

    Args:
        message (SupportMessage): Созданное сообщение пользователя.

    Returns:
        bool: True, если письмо отправлено успешно, иначе False.
    """
    event_key = _notify_support_admins(message)
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
        event_key=event_key,
        capture_event=False,
    )
    msg.attach_alternative(html_content, "text/html")
    msg.outbound_category = "support"
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
        event_key="support.reply",
    )
    msg.attach_alternative(html_content, "text/html")
    msg.outbound_category = "support"
    try:
        _send_with_timeout(msg)
        return True
    except Exception:
        logger.exception("send_user_support_reply failed")
        return False


def _send_user_support_reply_in_background(message_id: int) -> None:
    """Отправить email-ответ поддержки в фоновом потоке.

    Args:
        message_id (int): Идентификатор сообщения администратора.

    Returns:
        None: Функция ничего не возвращает.
    """
    connection.close()
    try:
        message = SupportMessage.objects.select_related("thread", "thread__user").get(
            pk=message_id
        )
    except SupportMessage.DoesNotExist:
        logger.warning("support reply message not found: id=%s", message_id)
        return
    except Exception:
        logger.exception("failed to load support reply message: id=%s", message_id)
        return

    send_user_support_reply(message)


def send_user_support_reply_async(message: SupportMessage) -> None:
    """Запустить отправку email-ответа поддержки в фоне.

    Args:
        message (SupportMessage): Сообщение администратора.

    Returns:
        None: Функция ничего не возвращает.
    """
    thread = threading.Thread(
        target=_send_user_support_reply_in_background,
        args=(int(message.pk),),
        daemon=True,
        name=f"support_reply_email_{message.pk}",
    )
    thread.start()
