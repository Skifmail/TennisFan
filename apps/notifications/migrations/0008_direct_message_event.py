"""Регистрирует уведомления о новых личных сообщениях без рассылки текста."""

from typing import Any

from django.db import migrations

ROWS = [
    {
        "key": "chat.direct_message",
        "title": "Новое личное сообщение",
        "group": "Личные чаты",
        "description": "Получателю каждого нового сообщения личного диалога, включая ответы. Без текста переписки. Источник push: inbox.",
        "inbox_enabled": True,
        "push_enabled": True,
        "email_enabled": False,
    }
]


def seed(apps: Any, schema_editor: Any) -> None:
    """Добавляет канал ЛК и push, не изменяя сохранённые настройки администратора.

    Args:
        apps: Исторический реестр моделей.
        schema_editor: Соединение применяемой миграции.
    """
    for row in ROWS:
        apps.get_model("notifications", "EventPolicy").objects.using(
            schema_editor.connection.alias
        ).get_or_create(
            key=row["key"],
            defaults={key: value for key, value in row.items() if key != "key"},
        )


class Migration(migrations.Migration):
    """Регистрирует политику доставки личных сообщений.

    Attributes:
        dependencies: Предыдущая схема центра уведомлений.
        operations: Идемпотентная регистрация события.
    """

    dependencies = [("notifications", "0007_merge_stringer_application")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
