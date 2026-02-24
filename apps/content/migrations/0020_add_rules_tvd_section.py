# Add RulesSection: Турнир выходного дня (ТВД) — editable in admin

from django.db import migrations


def add_tvd_section(apps, schema_editor):
    RulesSection = apps.get_model("content", "RulesSection")
    RulesSection.objects.get_or_create(
        slug="rules_tvd",
        defaults={"title": "Турнир выходного дня (ТВД)", "body": ""},
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0019_add_rules_rating_algorithm_section"),
    ]

    operations = [
        migrations.RunPython(add_tvd_section, noop),
    ]
