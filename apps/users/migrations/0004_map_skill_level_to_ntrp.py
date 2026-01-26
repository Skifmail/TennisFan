"""Map legacy skill levels to NTRP values."""

from __future__ import annotations

from django.db import migrations


LEGACY_TO_NTRP = {
    "beginner": "2.0",
    "intermediate": "3.0",
    "advanced": "4.0",
    "expert": "5.0",
}


def forwards(apps, schema_editor):
    Player = apps.get_model("users", "Player")
    for legacy_value, ntrp_value in LEGACY_TO_NTRP.items():
        Player.objects.filter(skill_level=legacy_value).update(skill_level=ntrp_value)


def backwards(apps, schema_editor):
    Player = apps.get_model("users", "Player")
    reverse_map = {value: key for key, value in LEGACY_TO_NTRP.items()}
    for ntrp_value, legacy_value in reverse_map.items():
        Player.objects.filter(skill_level=ntrp_value).update(skill_level=legacy_value)


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0003_notification"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
