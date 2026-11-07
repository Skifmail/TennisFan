"""Старые отметки отправки не выдаются за подтверждённую доставку новой очереди."""

from typing import Any

from django.db import migrations
from django.db.models import F


def mark_legacy(apps: Any, schema_editor: Any) -> None:
    """Сохранить историю без повторной отправки и без обещания полной доставки."""
    broadcast = apps.get_model("telegram_bot", "TelegramBroadcast")
    broadcast.objects.using(schema_editor.connection.alias).filter(
        sent_at__isnull=False
    ).update(status="legacy", prepared_at=F("sent_at"), finished_at=F("sent_at"))


class Migration(migrations.Migration):
    dependencies = [("telegram_bot", "0003_telegram_delivery_queue")]
    operations = [migrations.RunPython(mark_legacy, migrations.RunPython.noop)]
