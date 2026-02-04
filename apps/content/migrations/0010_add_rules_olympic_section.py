# Add RulesSection for Olympic format

from django.db import migrations


def add_olympic_section(apps, schema_editor):
    RulesSection = apps.get_model("content", "RulesSection")
    RulesSection.objects.get_or_create(
        slug="rules_olympic",
        defaults={"title": "Олимпийская система (утешительная сетка)", "body": ""},
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0009_populate_rules_sections"),
    ]

    operations = [
        migrations.RunPython(add_olympic_section, noop),
    ]
