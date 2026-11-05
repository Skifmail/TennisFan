"""Подключает сотрудников поддержки к прямым уведомлениям и добавляет ошибки."""

from typing import Any

from django.db import migrations

ROWS = [
    {
        "key": "support.bug_report",
        "title": "Новое сообщение об ошибке",
        "group": "Поддержка",
        "description": "Каждое новое сообщение об ошибке: ЛК и push всем активным администраторам и сотрудникам. Письмо — на настроенный адрес поддержки. Push открывает диалог.",
    }
]


def seed(apps: Any, schema_editor: Any) -> None:
    """Сохраняет выбранные каналы поддержки и наследует их для нового события."""
    policies = apps.get_model("notifications", "EventPolicy").objects.using(
        schema_editor.connection.alias
    )
    policy, _ = policies.get_or_create(
        key="support.admin_message",
        defaults={
            "title": "Новое обращение в поддержку",
            "group": "Поддержка",
            "description": "",
        },
    )
    policies.filter(pk=policy.pk).update(
        description="Каждое новое сообщение в поддержке: ЛК и push всем активным администраторам и сотрудникам. Письмо — на настроенный адрес поддержки. Push открывает диалог.",
    )
    for row in ROWS:
        policies.get_or_create(
            key=row["key"],
            defaults={
                **{key: value for key, value in row.items() if key != "key"},
                "push_enabled": policy.push_enabled,
                "email_enabled": policy.email_enabled,
                "inbox_enabled": policy.inbox_enabled,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("notifications", "0010_delivery_chat_room")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
