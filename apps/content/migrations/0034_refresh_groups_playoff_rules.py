# Переименовать раздел «ТВД» в «Группы + плей-офф» и обновить текст правил.

from django.db import migrations

from apps.content.rules_defaults import get_default_rules_body

RULES_SLUG = "rules_tvd"
RULES_TITLE = "Группы + плей-офф"


def refresh_groups_playoff_rules(apps, schema_editor):
    """Обновить название и HTML раздела правил «Группы + плей-офф»."""
    RulesSection = apps.get_model("content", "RulesSection")
    section = RulesSection.objects.filter(slug=RULES_SLUG).first()
    if section is None:
        return
    body = get_default_rules_body(RULES_SLUG)
    section.title = RULES_TITLE
    update_fields = ["title", "updated_at"]
    if body:
        section.body = body
        update_fields.append("body")
    section.save(update_fields=update_fields)


def noop(apps, schema_editor):
    """Откат не требуется: текст правил редактируемый."""


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0033_alter_stringerapplication_applicant"),
    ]

    operations = [
        migrations.RunPython(refresh_groups_playoff_rules, noop),
    ]
