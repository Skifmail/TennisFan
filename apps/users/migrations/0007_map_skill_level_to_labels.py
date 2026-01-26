"""Map legacy skill levels to 5 unified labels."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import migrations

LEGACY_MAP = {
    "beginner": "novice",
    "intermediate": "amateur",
    "advanced": "advanced",
    "expert": "professional",
}


def _map_skill_level(value: str) -> str:
    if value in LEGACY_MAP:
        return LEGACY_MAP[value]

    try:
        level = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value

    normalized = int(level.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if normalized <= 2:
        return "novice"
    if normalized <= 4:
        return "amateur"
    if normalized == 5:
        return "experienced"
    if normalized == 6:
        return "advanced"
    return "professional"


def forwards(apps, schema_editor):
    Player = apps.get_model("users", "Player")
    for player in Player.objects.all().only("id", "skill_level"):
        if not player.skill_level:
            continue
        new_value = _map_skill_level(player.skill_level)
        if new_value != player.skill_level:
            Player.objects.filter(id=player.id).update(skill_level=new_value)


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0006_alter_player_city"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
