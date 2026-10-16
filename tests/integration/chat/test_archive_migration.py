"""Перенос известных текстов с честной отметкой неполной ранней истории."""

from importlib import import_module
from uuid import uuid4

import pytest
from django.apps import apps
from django.db import connection
from django.utils import timezone

from apps.chat.models import ChatArchive, ChatArchivedMessage, ChatMessage, ChatRoom
from apps.core.models import City
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def test_migration_when_old_message_states_differ_should_preserve_known_text_once() -> (
    None
):
    """Перенос идемпотентен и не изображает восстановление уже очищенных текстов."""
    user = make_user(email="archive-migration@test.local", first_name="Исходный автор")
    room = ChatRoom.objects.create(
        city=City.objects.create(name="Город переноса архива")
    )
    messages = [
        ChatMessage.objects.create(room=room, author=user, text=text, client_id=uuid4())
        for text in ("Целый текст", "Изменённый текст", "")
    ]
    ChatMessage.objects.filter(pk=messages[1].pk).update(edited_at=timezone.now())
    ChatMessage.objects.filter(pk=messages[2].pk).update(deleted_at=timezone.now())
    # Подготовка старой схемы данных: живые записи существуют, снимков ещё нет.
    ChatArchive.objects.filter(room=room).delete()
    migration = import_module("apps.chat.migrations.0007_backfill_private_archives")
    with connection.schema_editor(atomic=False) as editor:
        migration.backfill_archives(apps, editor)
        migration.backfill_archives(apps, editor)
    archive = ChatArchive.objects.get(room=room)
    assert archive.messages.count() == 3
    assert list(
        archive.messages.order_by("source_message_id").values_list(
            "history_incomplete", flat=True
        )
    ) == [False, True, True]
    assert (
        archive.messages.get(source_message_id=messages[0].pk).versions.get().text
        == "Целый текст"
    )
    erased = ChatArchivedMessage.objects.get(
        archive=archive, source_message_id=messages[2].pk
    )
    assert erased.text == ""
    assert erased.history_incomplete
