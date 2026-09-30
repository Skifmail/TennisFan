"""Раздел правил падела."""

from django.db import migrations

from apps.content.rules_defaults import get_default_rules_body


def add_padel_rules(apps, schema_editor):
    """Создать раздел правил падела, если его ещё нет."""
    RulesSection = apps.get_model("content", "RulesSection")
    RulesSection.objects.get_or_create(
        slug="padel_rules",
        defaults={
            "title": "Правила падела",
            "body": get_default_rules_body("padel_rules"),
        },
    )


def remove_padel_rules(apps, schema_editor):
    """Удалить раздел правил падела."""
    RulesSection = apps.get_model("content", "RulesSection")
    RulesSection.objects.filter(slug="padel_rules").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0025_refresh_strength_hundredths_rules"),
    ]

    operations = [
        migrations.RunPython(add_padel_rules, remove_padel_rules),
    ]
