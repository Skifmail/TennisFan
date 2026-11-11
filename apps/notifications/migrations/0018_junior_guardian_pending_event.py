"""Событие проверки нового представителя детской лиги."""

from typing import Any

from django.db import migrations

ROWS = [
    {
        "key": "junior.guardian_pending",
        "title": "Детская лига: представитель на проверке",
        "description": (
            "Новая связь взрослого с ребёнком ожидает проверки. ЛК и push — "
            "сотрудникам с правом проверки представителей, письмо — на общий "
            "адрес администрации. Без документов и полной даты рождения."
        ),
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
                "title": row["title"],
                "group": "Детская лига",
                "description": row["description"],
                "email_enabled": True,
                "push_enabled": True,
                "inbox_enabled": True,
            },
        )


class Migration(migrations.Migration):
    """Регистрирует уведомление проверяющим о заявке представителя."""

    dependencies = [("notifications", "0017_telegram_delivery_queue")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
