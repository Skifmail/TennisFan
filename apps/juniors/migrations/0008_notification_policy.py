"""Подключение единого события детской лиги к центру уведомлений."""

from django.db import migrations

ROWS = [
    {
        "key": "junior.updated",
        "title": "Детская лига: изменение",
        "group": "Детская лига",
        "description": "Проверки, заявки, оплаты и результаты. Получатели — взрослые представители. Без документов и полной даты рождения.",
        "inbox_enabled": True,
        "push_enabled": True,
        "email_enabled": True,
    }
]


def seed(apps, schema_editor):
    apps.get_model("notifications", "EventPolicy").objects.using(
        schema_editor.connection.alias
    ).get_or_create(
        key="junior.updated",
        defaults={key: value for key, value in ROWS[0].items() if key != "key"},
    )


class Migration(migrations.Migration):
    dependencies = [("juniors", "0007_juniornotificationpolicy_juniorappeal")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
