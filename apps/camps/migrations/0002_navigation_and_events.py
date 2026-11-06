"""Добавляет раздел собственных кемпов в меню."""

from typing import Any

from django.db import migrations


def connect_camps(apps: Any, schema_editor: Any) -> None:
    """Добавляет пункт без изменения уже настроенного меню."""
    apps.get_model("navigation", "MenuItem").objects.using(
        schema_editor.connection.alias
    ).get_or_create(
        url="/camps/", defaults={"title": "Кемпы", "order": 65, "is_active": True}
    )


class Migration(migrations.Migration):
    """Дополняет навигацию после подключения политик событий."""

    dependencies = [
        ("camps", "0001_initial"),
        ("navigation", "0011_add_city_chats_menu_item"),
        ("notifications", "0012_camp_events"),
    ]
    operations = [migrations.RunPython(connect_camps, migrations.RunPython.noop)]
