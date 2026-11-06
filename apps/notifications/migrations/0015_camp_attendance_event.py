"""Событие ответа участника о приезде на кемп."""

from typing import Any

from django.db import migrations

ROWS = [
    {
        "key": "camp.attendance_confirmed",
        "title": "Кемп: участник подтвердил поездку",
        "description": "Команде о подтверждении приезда участником без оплаты.",
    },
]


def seed(apps: Any, schema_editor: Any) -> None:
    """Добавляет политику каналов, не перезаписывая существующие настройки."""
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

    policies.filter(key="camp.departure_reminder").update(
        description="Участникам с местами за 7, 3 и 1 день до начала: просьба подтвердить приезд либо напоминание о подтверждённой поездке."
    )


class Migration(migrations.Migration):
    """Регистрирует уведомление координатору о подтверждении поездки."""

    dependencies = [("notifications", "0014_camp_reservation_events")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
