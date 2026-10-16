"""Защита архива при удалении комнаты через любой поддерживаемый ORM-путь."""

from typing import Any

from django.db.models.signals import post_save, pre_delete

from .archive import ensure_archive, preserve_room_archive
from .models import ChatRoom


def _room_saved(
    sender: type[ChatRoom], instance: ChatRoom, using: str, raw: bool, **kwargs: Any
) -> None:
    if not raw and instance.is_created:
        ensure_archive(instance, using=using)


def _room_deleted(
    sender: type[ChatRoom], instance: ChatRoom, using: str, **kwargs: Any
) -> None:
    preserve_room_archive(instance, using=using)


def connect_archive_signals() -> None:
    """Подключает обработчики с постоянными ключами без повторной регистрации."""
    post_save.connect(
        _room_saved, sender=ChatRoom, dispatch_uid="chat.archive.room.saved"
    )
    pre_delete.connect(
        _room_deleted, sender=ChatRoom, dispatch_uid="chat.archive.room.deleted"
    )
