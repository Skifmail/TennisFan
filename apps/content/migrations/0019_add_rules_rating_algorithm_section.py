# Add RulesSection: Алгоритм расчета рейтинга (editable in admin)

from django.db import migrations


def add_rating_algorithm_section(apps, schema_editor):
    RulesSection = apps.get_model("content", "RulesSection")
    RulesSection.objects.get_or_create(
        slug="rules_rating_algorithm",
        defaults={"title": "Алгоритм расчета рейтинга", "body": ""},
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0018_rename_rules_fan_title"),
    ]

    operations = [
        migrations.RunPython(add_rating_algorithm_section, noop),
    ]
