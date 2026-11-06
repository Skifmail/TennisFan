"""Уведомления о временной брони кемпа без предоплаты."""

from typing import Any

from django.db import migrations

ROWS = [
    {
        "key": "camp.reservation_updated",
        "title": "Кемп: временная бронь",
        "description": "Выдача или продление координатором временной брони без оплаты.",
    },
    {
        "key": "camp.reservation_expired",
        "title": "Кемп: срок брони истёк",
        "description": "Участнику и координатору об освобождении мест и переводе заявки в лист ожидания.",
    },
]


def seed(apps: Any, schema_editor: Any) -> None:
    """Регистрирует события, не перезаписывая настройки существующих каналов."""
    policies = apps.get_model("notifications", "EventPolicy").objects.using(
        schema_editor.connection.alias
    )
    for row in ROWS:
        policies.get_or_create(
            key=row["key"],
            defaults={
                **{key: value for key, value in row.items() if key != "key"},
                "group": "Кемпы",
                "email_enabled": True,
                "push_enabled": True,
                "inbox_enabled": True,
            },
        )


class Migration(migrations.Migration):
    """Расширяет общий каталог событий кемпов."""

    dependencies = [("notifications", "0013_camp_followup_events")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
