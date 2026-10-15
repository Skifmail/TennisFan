"""Добавляет раздел городских чатов, сохраняя существующие пункты меню."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def add_chat_menu(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Добавляет ссылку, не меняя настройки уже существующей.

    Args:
        apps: Исторический реестр моделей.
        schema_editor: Редактор текущей базы.
    """
    menu = apps.get_model("navigation", "MenuItem")
    menu.objects.using(schema_editor.connection.alias).get_or_create(
        url="/chats/",
        defaults={"title": "Чаты городов", "order": 85, "is_active": True},
    )


class Migration(migrations.Migration):
    """Безопасно расширяет управляемую навигацию платформы."""

    dependencies = [
        ("navigation", "0010_add_padel_menu_item"),
        ("chat", "0001_initial"),
    ]
    operations = [migrations.RunPython(add_chat_menu, migrations.RunPython.noop)]
