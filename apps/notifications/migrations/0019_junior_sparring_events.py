"""События товарищеских спаррингов детской лиги."""

from typing import Any

from django.db import migrations

ROWS = [
    {
        "key": "junior.sparring_response",
        "title": "Детская лига: отклик на спарринг",
        "description": (
            "Представитель ребёнка получил отклик на заявку о товарищеской игре. "
            "Текст содержит только публичную метку чужого ребёнка или имя "
            "взрослого, без контактов и даты рождения."
        ),
    },
    {
        "key": "junior.sparring_accepted",
        "title": "Детская лига: спарринг согласован",
        "description": (
            "Отклик принят, игра согласована. Обе стороны получают уведомление "
            "и открывают контакты взрослых участников в самой заявке."
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
    """Регистрирует уведомления об откликах и согласовании спаррингов."""

    dependencies = [("notifications", "0018_junior_guardian_pending_event")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
