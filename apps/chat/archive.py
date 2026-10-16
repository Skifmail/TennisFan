"""Атомарные снимки переписки для закрытого служебного архива."""

from typing import Final, Literal

from django.db import DEFAULT_DB_ALIAS, transaction
from django.db.models import Max, Subquery
from django.utils import timezone
from loguru import logger

from apps.users.models import User

from .models import (
    ChatArchive,
    ChatArchivedMessage,
    ChatMessage,
    ChatMessageVersion,
    ChatRoom,
)

ARCHIVE_BATCH_SIZE: Final = 500
INITIAL_SEQUENCE: Final = 1
ArchiveAction = Literal["created", "edited", "deleted"]


def _label(user: User | None) -> str:
    if user is None:
        return "Удалённый аккаунт"
    return user.get_full_name().strip() or f"Участник #{user.pk}"


def ensure_archive(room: ChatRoom, *, using: str = DEFAULT_DB_ALIAS) -> ChatArchive:
    """Получает архив именно этого поколения комнаты, не переиспользуя архив города.

    Args:
        room: Исходная комната.
        using: База данных операции.

    Returns:
        Независимый контейнер с зафиксированными названием и временем создания.
    """
    archive, _ = ChatArchive.objects.using(using).get_or_create(
        source_room_id=room.pk,
        defaults={
            "room_id": room.pk,
            "city_label": str(room),
            "room_created_at": room.created_at,
        },
    )
    return archive


def ensure_message_snapshot(
    message: ChatMessage, *, using: str = DEFAULT_DB_ALIAS, created: bool = False
) -> ChatArchivedMessage:
    """Сохраняет исходный текст до его перезаписи, помечая неполные старые данные.

    Args:
        message: Сообщение до редактирования или удаления.
        using: База данных операции.
        created: Это новая подтверждённая отправка, а не перенос старой записи.

    Returns:
        Независимый снимок с начальной версией.
    """
    with transaction.atomic(using=using):
        ChatRoom.objects.using(using).select_for_update().get(pk=message.room_id)
        archive = ensure_archive(message.room, using=using)
        saved, first = ChatArchivedMessage.objects.using(using).get_or_create(
            archive=archive,
            source_message_id=message.pk,
            defaults={
                "author_reference": message.author_id,
                "author_label": _label(message.author),
                "text": message.text,
                "sent_at": message.created_at,
                "edited_at": message.edited_at,
                "deleted_at": message.deleted_at,
                "history_incomplete": bool(message.edited_at or message.deleted_at),
            },
        )
        if first:
            ChatMessageVersion.objects.using(using).create(
                message=saved,
                sequence=INITIAL_SEQUENCE,
                action="created" if created else "imported",
                text=message.text,
                actor_reference=message.author_id if created else None,
                actor_label=_label(message.author) if created else "",
                occurred_at=message.created_at if created else timezone.now(),
            )
        return saved


def record_message_change(
    *, message: ChatMessage, actor: User, action: ArchiveAction, text: str
) -> None:
    """Добавляет версию в транзакции отправки, редактирования либо удаления.

    Args:
        message: Изменённая публичная копия.
        actor: Реальный инициатор действия, в том числе удаляющий модератор.
        action: Тип операции.
        text: Содержимое до удаления либо после редактирования.
    """
    using = message._state.db or DEFAULT_DB_ALIAS
    with transaction.atomic(using=using):
        saved = ensure_message_snapshot(
            message, using=using, created=action == "created"
        )
        if action == "created":
            return
        latest = (
            saved.versions.using(using).aggregate(latest=Max("sequence"))["latest"] or 0
        )
        ChatMessageVersion.objects.using(using).create(
            message=saved,
            sequence=latest + INITIAL_SEQUENCE,
            action=action,
            text=text,
            actor_reference=actor.pk,
            actor_label=_label(actor),
            occurred_at=timezone.now(),
        )
        saved.text = text
        saved.edited_at = message.edited_at
        saved.deleted_at = message.deleted_at
        saved.save(using=using, update_fields=["text", "edited_at", "deleted_at"])


def preserve_room_archive(room: ChatRoom, *, using: str = DEFAULT_DB_ALIAS) -> None:
    """Отсоединяет архив перед удалением комнаты, сохраняя ещё не перенесённые записи.

    Args:
        room: Удаляемая комната.
        using: База данных удаления.
    """
    with transaction.atomic(using=using):
        ChatRoom.objects.using(using).select_for_update().filter(pk=room.pk).first()
        if (
            not room.is_created
            and not room.messages.using(using).exists()
            and not ChatArchive.objects.using(using)
            .filter(source_room_id=room.pk)
            .exists()
        ):
            return
        archive = ensure_archive(room, using=using)
        archived_ids = archive.messages.using(using).values("source_message_id")
        missing = (
            room.messages.using(using)
            .exclude(pk__in=Subquery(archived_ids))
            .select_related("room__city", "author")
        )
        for message in missing.iterator(chunk_size=ARCHIVE_BATCH_SIZE):
            ensure_message_snapshot(message, using=using)
        # Collector мог собрать SET_NULL до появления контейнера: отвязываем явно.
        archive.room = None
        archive.room_removed_at = timezone.now()
        archive.save(using=using, update_fields=["room", "room_removed_at"])
        logger.info(
            "Архив сохранён после удаления городского чата",
            room_id=room.pk,
            archive_id=str(archive.pk),
        )
