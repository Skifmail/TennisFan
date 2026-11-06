"""Политики восстановления доступа, отмены и напоминаний кемпа."""

from typing import Any

from django.db import migrations

ROWS = [
    {
        "key": "camp.guest_access",
        "title": "Кемп: восстановление доступа",
        "description": "Одноразовая ссылка только на email исходных гостевых заявок; без копии секрета в ЛК и push.",
    },
    {
        "key": "camp.cancellation_requested",
        "title": "Кемп: запрос на отмену",
        "description": "Координатору и заявителю после отправки запроса на отмену.",
    },
    {
        "key": "camp.cancellation_updated",
        "title": "Кемп: решение по отмене",
        "description": "Заявителю после решения координатора.",
    },
    {
        "key": "camp.departure_reminder",
        "title": "Кемп: скоро поездка",
        "description": "Подтверждённым участникам за 7 дней и за сутки до начала.",
    },
    {
        "key": "camp.payment_reminder",
        "title": "Кемп: напоминание о доплате",
        "description": "При положительном остатке за 3 дня, за день, в срок оплаты и однократно после просрочки.",
    },
    {
        "key": "camp.unanswered_reminder",
        "title": "Кемп: заявка без ответа",
        "description": "Координатору о новой заявке без ответа больше суток.",
    },
]


def seed(apps: Any, schema_editor: Any) -> None:
    """Добавляет события без перезаписи настроек существующих каналов."""
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
                "inbox_enabled": row["key"] != "camp.guest_access",
                "push_enabled": row["key"] != "camp.guest_access",
            },
        )


class Migration(migrations.Migration):
    """Расширяет общий каталог событий."""

    dependencies = [("notifications", "0012_camp_events")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
