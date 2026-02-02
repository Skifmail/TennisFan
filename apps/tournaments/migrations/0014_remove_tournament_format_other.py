# Generated manually: remove TournamentFormat.OTHER

from django.db import migrations, models


def migrate_other_to_fan(apps, schema_editor):
    """Migrate existing tournaments with format='other' to 'single_elimination'."""
    Tournament = apps.get_model("tournaments", "Tournament")
    Tournament.objects.filter(format="other").update(format="single_elimination")


def reverse_migrate(apps, schema_editor):
    """Reverse: no-op (cannot restore 'other' value)."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0013_remove_match_schedule"),
    ]

    operations = [
        migrations.RunPython(migrate_other_to_fan, reverse_migrate),
        migrations.AlterField(
            model_name="tournament",
            name="format",
            field=models.CharField(
                choices=[("single_elimination", "FAN (одноэтапная сетка)")],
                default="single_elimination",
                help_text="FAN: одноэтапная сетка, посев по рейтингу, очки при вылете.",
                max_length=20,
                verbose_name="Формат",
            ),
        ),
    ]
