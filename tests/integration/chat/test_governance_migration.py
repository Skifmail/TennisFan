"""Недеструктивный переход существующих городских комнат."""

from importlib import import_module
from uuid import uuid4

import pytest
from django.apps import apps
from django.db import connection

from apps.chat.models import ChatMembership, ChatMessage, ChatRoom
from apps.core.models import City
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def test_migration_when_legacy_rooms_differ_should_preserve_history_members_and_closures() -> (
    None
):
    """Заготовкой становится только открытая комната без каких-либо участников и истории."""
    rooms = [
        ChatRoom.objects.create(
            city=City.objects.create(name=f"Миграционный город {index}")
        )
        for index in range(4)
    ]
    user = make_user(email="migration-member@test.local")
    ChatMembership.objects.create(
        room=rooms[1], user=user, is_active=False, is_moderator=True
    )
    ChatMessage.objects.create(
        room=rooms[2], author=user, text="Сохранённая история", client_id=uuid4()
    )
    rooms[3].is_active = False
    rooms[3].save(update_fields=["is_active"])
    migration = import_module("apps.chat.migrations.0005_mark_unused_rooms")
    with connection.schema_editor(atomic=False) as editor:
        migration.mark_unused_rooms(apps, editor)
    for room in rooms:
        room.refresh_from_db()
    assert not rooms[0].is_created
    assert all(room.is_created for room in rooms[1:])
    assert rooms[2].messages.get().text == "Сохранённая история"
    assert rooms[1].memberships.get().is_moderator
    assert not rooms[3].is_active
