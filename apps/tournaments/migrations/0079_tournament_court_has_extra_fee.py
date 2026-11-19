from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def preserve_existing_group_tournament_display(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Сохранить прежний показ взноса в существующих турнирах с группами."""
    tournament = apps.get_model("tournaments", "Tournament")
    tournament.objects.using(schema_editor.connection.alias).filter(
        format="weekend_day"
    ).update(court_has_extra_fee=False)


class Migration(migrations.Migration):
    dependencies = [
        ("tournaments", "0078_alter_tournament_allow_postpayment_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="tournament",
            name="court_has_extra_fee",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Показывает предупреждение рядом со вступительным взносом. "
                    "Снимите галочку, если аренда корта включена во взнос."
                ),
                verbose_name="Корт оплачивается отдельно",
            ),
        ),
        migrations.RunPython(
            preserve_existing_group_tournament_display, migrations.RunPython.noop
        ),
    ]
