"""Переносит только сохранившиеся тексты, не выдумывая утраченные редакции."""

from typing import Final

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Q, Subquery
from django.utils import timezone

BATCH_SIZE: Final = 500
INITIAL_SEQUENCE: Final = 1


def backfill_archives(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Переносит исторические сообщения ограниченными пакетами без удаления источников.

    Args:
        apps: Исторические модели схемы.
        schema_editor: Редактор целевой базы данных.
    """
    room_model = apps.get_model("chat", "ChatRoom")
    archive_model = apps.get_model("chat", "ChatArchive")
    message_model = apps.get_model("chat", "ChatArchivedMessage")
    version_model = apps.get_model("chat", "ChatMessageVersion")
    using = schema_editor.connection.alias
    imported_at = timezone.now()
    rooms = (
        room_model.objects.using(using)
        .filter(Q(is_created=True) | Q(messages__isnull=False))
        .select_related("city")
        .distinct()
        .order_by("pk")
    )
    for room in rooms.iterator(chunk_size=BATCH_SIZE):
        city_label = (
            f"{room.city.name}, {room.city.region}"
            if room.city.region
            else room.city.name
        )
        archive, _ = archive_model.objects.using(using).get_or_create(
            source_room_id=room.pk,
            defaults={
                "room_id": room.pk,
                "city_label": city_label,
                "room_created_at": room.created_at,
            },
        )
        existing = (
            message_model.objects.using(using)
            .filter(archive=archive)
            .values("source_message_id")
        )
        messages = (
            room.messages.using(using)
            .exclude(pk__in=Subquery(existing))
            .select_related("author")
            .order_by("pk")
        )
        batch = []
        for message in messages.iterator(chunk_size=BATCH_SIZE):
            author = message.author
            label = (
                (
                    f"{author.first_name} {author.last_name}".strip()
                    or f"Участник #{author.pk}"
                )
                if author
                else "Удалённый аккаунт"
            )
            batch.append(
                message_model(
                    archive=archive,
                    source_message_id=message.pk,
                    author_reference=message.author_id,
                    author_label=label,
                    text=message.text,
                    sent_at=message.created_at,
                    edited_at=message.edited_at,
                    deleted_at=message.deleted_at,
                    history_incomplete=bool(message.edited_at or message.deleted_at),
                )
            )
            if len(batch) >= BATCH_SIZE:
                saved = message_model.objects.using(using).bulk_create(
                    batch, batch_size=BATCH_SIZE
                )
                version_model.objects.using(using).bulk_create(
                    [
                        version_model(
                            message=entry,
                            sequence=INITIAL_SEQUENCE,
                            action="imported",
                            text=entry.text,
                            occurred_at=imported_at,
                        )
                        for entry in saved
                    ],
                    batch_size=BATCH_SIZE,
                )
                batch = []
        if batch:
            saved = message_model.objects.using(using).bulk_create(
                batch, batch_size=BATCH_SIZE
            )
            version_model.objects.using(using).bulk_create(
                [
                    version_model(
                        message=entry,
                        sequence=INITIAL_SEQUENCE,
                        action="imported",
                        text=entry.text,
                        occurred_at=imported_at,
                    )
                    for entry in saved
                ],
                batch_size=BATCH_SIZE,
            )


class Migration(migrations.Migration):
    """Сохраняет независимые снимки доступной истории переписки."""

    dependencies = [
        ("chat", "0006_chatarchive_chatarchivedmessage_chatmessageversion_and_more")
    ]
    operations = [migrations.RunPython(backfill_archives, migrations.RunPython.noop)]
