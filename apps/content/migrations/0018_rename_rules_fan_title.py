# Rename RulesSection title: "FAN (одноэтапная сетка)" -> "Одноэтапная сетка"

from django.db import migrations


def update_rules_fan_title(apps, schema_editor):
    RulesSection = apps.get_model("content", "RulesSection")
    RulesSection.objects.filter(slug="rules_fan").update(title="Одноэтапная сетка")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0017_alter_aboutus_options_alter_contactpage_options_and_more"),
    ]

    operations = [
        migrations.RunPython(update_rules_fan_title, noop),
    ]
