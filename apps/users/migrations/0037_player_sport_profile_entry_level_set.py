"""Зафиксировать одноразовый стартовый уровень падела."""

from django.db import migrations, models


def mark_existing_padel_profiles(apps, schema_editor):
    """Считать уже созданные профили падела пройденным онбордингом."""
    Profile = apps.get_model("users", "PlayerSportProfile")
    Profile.objects.filter(sport="padel").update(entry_level_set=True)


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0036_backfill_tennis_sport_profiles"),
    ]

    operations = [
        migrations.AddField(
            model_name="playersportprofile",
            name="entry_level_set",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Игрок уже зафиксировал входной уровень. "
                    "Дальше сила меняется матчами или администратором."
                ),
                verbose_name="Стартовый уровень задан",
            ),
        ),
        migrations.RunPython(mark_existing_padel_profiles, migrations.RunPython.noop),
    ]
