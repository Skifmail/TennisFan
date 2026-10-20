"""Объединяет миграции заявок стрингеров и состояния рассылок."""

from django.db import migrations
from django.db.migrations.operations.base import Operation


class Migration(migrations.Migration):
    """Соединяет независимые ветки без изменения данных или схемы."""

    dependencies = [
        ("notifications", "0004_stringer_application_event"),
        ("notifications", "0006_broadcast_counter_defaults"),
    ]
    operations: list[Operation] = []
