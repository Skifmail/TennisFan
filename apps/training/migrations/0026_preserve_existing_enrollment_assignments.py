"""Сохранить доступ текущих тренеров к заявкам, созданным до распределения."""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def preserve_assignments(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Пакетно назначить старые заявки владельцам тренировок без уведомлений."""
    enrollment_model = apps.get_model("training", "TrainingEnrollment")
    alias = schema_editor.connection.alias
    last_pk = 0
    while True:
        batch = list(
            enrollment_model.objects.using(alias)
            .filter(pk__gt=last_pk)
            .select_related("training__coach")
            .order_by("pk")[:1000]
        )
        if not batch:
            break
        for enrollment in batch:
            coach = enrollment.training.coach
            enrollment.requested_coach_id = coach.pk if coach else None
            enrollment.requested_coach_name = coach.name if coach else ""
            enrollment.assigned_coach_id = coach.pk if coach else None
            enrollment.assigned_at = enrollment.created_at if coach else None
        enrollment_model.objects.using(alias).bulk_update(
            batch,
            [
                "requested_coach",
                "requested_coach_name",
                "assigned_coach",
                "assigned_at",
            ],
            batch_size=1000,
        )
        last_pk = batch[-1].pk


class Migration(migrations.Migration):
    dependencies = [("training", "0025_enrollment_distribution")]
    operations = [migrations.RunPython(preserve_assignments, migrations.RunPython.noop)]
