# Add RulesSection: Правила посева (editable in admin)

from django.db import migrations


def add_seeding_section(apps, schema_editor):
    RulesSection = apps.get_model("content", "RulesSection")
    RulesSection.objects.get_or_create(
        slug="rules_seeding",
        defaults={"title": "Правила посева", "body": ""},
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0010_add_rules_olympic_section"),
    ]

    operations = [
        migrations.RunPython(add_seeding_section, noop),
    ]
