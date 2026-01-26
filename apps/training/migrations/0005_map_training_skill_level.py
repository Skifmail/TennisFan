"""Map training skill levels to unified labels."""
from __future__ import annotations

from django.db import migrations

LEVEL_MAP = {
    "beginner": "novice",
    "intermediate": "amateur",
    "advanced": "advanced",
    "all": "amateur",
}


def forwards(apps, schema_editor):
    Training = apps.get_model("training", "Training")
    for old_value, new_value in LEVEL_MAP.items():
        Training.objects.filter(skill_level=old_value).update(skill_level=new_value)


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("training", "0004_alter_coach_city_alter_training_city"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
