"""Добавляет каналы уведомления о новой заявке стрингера."""

from typing import Any

from django.db import migrations

ROWS = [
    {
        "key": "stringer.application_submitted",
        "title": "Новая заявка стрингера",
        "group": "Стрингеры",
        "description": "Активным администраторам с правом рассмотрения заявок: email, ЛК и push со ссылкой на заявку.",
    }
]


def seed(apps: Any, schema_editor: Any) -> None:
    """Регистрирует событие без изменения существующих настроек каналов."""
    policy = apps.get_model("notifications", "EventPolicy")
    for row in ROWS:
        policy.objects.using(schema_editor.connection.alias).get_or_create(
            key=row["key"],
            defaults={key: value for key, value in row.items() if key != "key"},
        )


class Migration(migrations.Migration):
    """Сохраняет политику нового события."""

    dependencies = [
        ("notifications", "0003_pushdevice_subscription_id_pushconfirmation")
    ]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
