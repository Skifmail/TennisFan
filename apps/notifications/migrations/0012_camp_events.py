"""Регистрирует события собственных кемпов TennisFan."""

from typing import Any

from django.db import migrations

ROWS = [
    {
        "key": "camp.application_submitted",
        "title": "Кемп: новая заявка",
        "group": "Кемпы",
        "description": "Новая заявка: координатору заезда и авторизованному заявителю.",
    },
    {
        "key": "camp.application_updated",
        "title": "Кемп: статус участия",
        "group": "Кемпы",
        "description": "Изменение статуса участия: заявителю.",
    },
]


def seed(apps: Any, schema_editor: Any) -> None:
    """Добавляет разрешения, сохраняя настройки существующих событий."""
    policies = apps.get_model("notifications", "EventPolicy").objects.using(
        schema_editor.connection.alias
    )
    for row in ROWS:
        policies.get_or_create(
            key=row["key"],
            defaults={
                **{key: value for key, value in row.items() if key != "key"},
                "push_enabled": True,
                "email_enabled": True,
                "inbox_enabled": True,
            },
        )


class Migration(migrations.Migration):
    """Дополняет управляемый каталог каналов уведомлений."""

    dependencies = [("notifications", "0011_support_admin_push")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
