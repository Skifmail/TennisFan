"""Без удаления данных отделяет технические заготовки от существующих сообществ."""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Exists, OuterRef


def mark_unused_rooms(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Помечает открытые комнаты без истории, участников и управляющих заготовками.

    Args:
        apps: Исторический реестр моделей миграции.
        schema_editor: Редактор схемы целевой базы.
    """
    room = apps.get_model("chat", "ChatRoom")
    membership = apps.get_model("chat", "ChatMembership")
    message = apps.get_model("chat", "ChatMessage")
    database = schema_editor.connection.alias
    room.objects.using(database).annotate(
        has_members=Exists(
            membership.objects.using(database).filter(room_id=OuterRef("pk"))
        ),
        has_messages=Exists(
            message.objects.using(database).filter(room_id=OuterRef("pk"))
        ),
    ).filter(
        is_active=True,
        administrator__isnull=True,
        has_members=False,
        has_messages=False,
    ).update(
        is_created=False
    )


class Migration(migrations.Migration):
    """Сохраняет все записи, меняя лишь доступность явного создания сообщества."""

    dependencies = [
        ("chat", "0004_chatroom_administrator_chatroom_is_created_and_more")
    ]
    operations = [migrations.RunPython(mark_unused_rooms, migrations.RunPython.noop)]
