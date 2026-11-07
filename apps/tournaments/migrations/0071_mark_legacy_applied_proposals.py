"""Не переигрывать старые принятые заявки после повторного открытия матча."""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.utils import timezone


def mark_accepted(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Пометить прежние принятые заявки временем миграции, а не выдуманной датой игры."""
    proposal = apps.get_model("tournaments", "MatchResultProposal")
    rows = proposal.objects.using(schema_editor.connection.alias).filter(
        status="accepted", applied_at__isnull=True
    )
    migrated_at = timezone.now()
    while ids := list(rows.order_by("pk").values_list("pk", flat=True)[:1000]):
        rows.filter(pk__in=ids).update(applied_at=migrated_at)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("tournaments", "0070_matchresultproposal_applied_at")]
    operations = [migrations.RunPython(mark_accepted, migrations.RunPython.noop)]
