"""Сервисная логика диалогов поддержки."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from django.db.models import QuerySet, Sum
from django.utils import timezone

from .models import SupportMessage, SupportThread


@dataclass(frozen=True)
class SupportAuthor:
    """Данные автора обращения.

    Args:
        user_id (Optional[int]): ID авторизованного пользователя.
        guest_email (str): Email гостя.
        guest_name (str): Имя гостя.
        guest_session_key (str): Ключ сессии гостя.
    """

    user_id: int | None
    guest_email: str
    guest_name: str
    guest_session_key: str


SUPPORT_MESSAGE_EDIT_WINDOW = timedelta(minutes=15)


def can_edit_support_message(
    message: SupportMessage, *, now: datetime | None = None
) -> bool:
    """Проверить, можно ли редактировать сообщение.

    Args:
        message (SupportMessage): Сообщение поддержки.
        now (datetime | None): Текущее время для проверки окна редактирования.

    Returns:
        bool: True, если сообщение попадает в окно редактирования.
    """
    now_value = now or timezone.now()
    return bool((now_value - message.created_at) <= SUPPORT_MESSAGE_EDIT_WINDOW)


def can_delete_support_message(
    message: SupportMessage, *, now: datetime | None = None
) -> bool:
    """Проверить, можно ли удалить сообщение.

    Args:
        message (SupportMessage): Сообщение поддержки.
        now (datetime | None): Текущее время для проверки окна удаления.

    Returns:
        bool: True, если сообщение можно удалить.
    """
    return True


def _resolve_thread(author: SupportAuthor) -> SupportThread:
    """Найти или создать диалог поддержки для автора.

    Args:
        author (SupportAuthor): Данные автора сообщения.

    Returns:
        SupportThread: Найденный или созданный диалог.
    """
    now = timezone.now()
    if author.user_id:
        thread, _ = SupportThread.objects.get_or_create(
            user_id=author.user_id,
            defaults={
                "last_message_at": now,
            },
        )
        return cast(SupportThread, thread)

    if author.guest_session_key:
        thread = SupportThread.objects.filter(
            user__isnull=True,
            guest_session_key=author.guest_session_key,
        ).first()
        if thread:
            return cast(SupportThread, thread)

    thread = SupportThread.objects.create(
        user_id=None,
        guest_email=author.guest_email,
        guest_name=author.guest_name,
        guest_session_key=author.guest_session_key,
        last_message_at=now,
    )
    return cast(SupportThread, thread)


def create_user_message(
    *, author: SupportAuthor, text: str, subject: str = ""
) -> SupportMessage:
    """Создать сообщение пользователя и обновить счетчики диалога.

    Args:
        author (SupportAuthor): Данные автора сообщения.
        text (str): Текст сообщения.
        subject (str): Тема обращения.

    Returns:
        SupportMessage: Созданное сообщение.
    """
    thread = _resolve_thread(author)
    message = SupportMessage.objects.create(
        thread=thread,
        user_id=author.user_id,
        guest_name=author.guest_name,
        guest_contact=author.guest_email,
        subject=subject[:200],
        text=text,
        is_from_admin=False,
    )
    thread.last_message_at = message.created_at
    thread.admin_unread_count = max(thread.admin_unread_count, 0) + 1
    thread.save(update_fields=["last_message_at", "admin_unread_count", "updated_at"])
    return cast(SupportMessage, message)


def create_admin_reply(*, thread: SupportThread, text: str) -> SupportMessage:
    """Создать ответ администратора и обновить счетчики диалога.

    Args:
        thread (SupportThread): Диалог поддержки.
        text (str): Текст ответа администратора.

    Returns:
        SupportMessage: Созданное сообщение администратора.
    """
    message = SupportMessage.objects.create(
        thread=thread,
        user=thread.user,
        guest_name=thread.guest_name,
        guest_contact=thread.guest_email,
        text=text,
        is_from_admin=True,
    )
    thread.last_message_at = message.created_at
    thread.admin_unread_count = 0
    thread.user_unread_count = max(thread.user_unread_count, 0) + 1
    thread.save(
        update_fields=[
            "last_message_at",
            "admin_unread_count",
            "user_unread_count",
            "updated_at",
        ]
    )
    return cast(SupportMessage, message)


def update_support_message_text(
    *, message: SupportMessage, text: str
) -> SupportMessage:
    """Обновить текст сообщения поддержки и отметить факт редактирования.

    Args:
        message (SupportMessage): Сообщение для обновления.
        text (str): Новый текст сообщения.

    Returns:
        SupportMessage: Обновленный объект сообщения.
    """
    message.text = text
    message.edited_at = timezone.now()
    message.save(update_fields=["text", "edited_at"])
    return cast(SupportMessage, message)


def delete_support_message(*, message: SupportMessage) -> None:
    """Удалить сообщение поддержки и синхронизировать агрегаты диалога.

    Args:
        message (SupportMessage): Сообщение для удаления.

    Returns:
        None: Функция ничего не возвращает.
    """
    thread = message.thread
    latest_before_delete = thread.messages.order_by("-created_at", "-id").first()
    is_latest_message = bool(
        latest_before_delete and latest_before_delete.id == message.id
    )
    is_admin_message = bool(message.is_from_admin)
    message.delete()

    update_fields: list[str] = ["updated_at"]
    if is_admin_message and thread.user_unread_count > 0:
        # В модели нет per-message read-флага, поэтому корректируем счетчик
        # по нижней границе, чтобы не уходить в отрицательные значения.
        thread.user_unread_count = max(thread.user_unread_count - 1, 0)
        update_fields.append("user_unread_count")

    if is_latest_message:
        latest_message = thread.messages.order_by("-created_at", "-id").first()
        thread.last_message_at = (
            latest_message.created_at if latest_message else timezone.now()
        )
        update_fields.append("last_message_at")

    thread.save(update_fields=update_fields)


def mark_thread_read_by_user(thread: SupportThread) -> None:
    """Пометить ответы администратора как прочитанные пользователем.

    Args:
        thread (SupportThread): Диалог поддержки.

    Returns:
        None: Функция ничего не возвращает.
    """
    if thread.user_unread_count == 0:
        return
    thread.user_unread_count = 0
    thread.save(update_fields=["user_unread_count", "updated_at"])


def mark_thread_read_by_admin(thread: SupportThread) -> None:
    """Пометить сообщения пользователя как прочитанные администратором.

    Args:
        thread (SupportThread): Диалог поддержки.

    Returns:
        None: Функция ничего не возвращает.
    """
    if thread.admin_unread_count == 0:
        return
    thread.admin_unread_count = 0
    thread.save(update_fields=["admin_unread_count", "updated_at"])


def get_threads_for_admin(*, limit: int = 50) -> QuerySet[SupportThread]:
    """Вернуть диалоги поддержки для административного интерфейса.

    Args:
        limit (int): Максимальное число диалогов.

    Returns:
        QuerySet[SupportThread]: Набор диалогов.
    """
    return (
        SupportThread.objects.select_related("user")
        .prefetch_related("messages")
        .order_by("-last_message_at")[:limit]
    )


def get_unread_count_for_admin() -> int:
    """Вернуть суммарное количество непрочитанных сообщений для админов.

    Returns:
        int: Количество непрочитанных сообщений.
    """
    value = SupportThread.objects.aggregate(total=Sum("admin_unread_count"))["total"]
    return int(value or 0)


def get_unread_count_for_user(*, thread: SupportThread | None) -> int:
    """Вернуть количество непрочитанных ответов администратора.

    Args:
        thread (SupportThread | None): Диалог поддержки.

    Returns:
        int: Количество непрочитанных сообщений.
    """
    if thread is None:
        return 0
    return int(thread.user_unread_count)
