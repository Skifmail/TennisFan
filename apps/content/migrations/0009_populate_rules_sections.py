# Data migration: create RulesSection records (body empty — use template fallback or edit in admin)

from django.db import migrations


def create_rules_sections(apps, schema_editor):
    """Create default rules section records; content can be edited in admin or left empty for template fallback."""
    RulesSection = apps.get_model("content", "RulesSection")
    sections = [
        ("tennis_rules", "Правила тенниса"),
        ("rules_fan", "FAN (одноэтапная сетка)"),
        ("rules_round_robin", "Круговой турнир"),
        ("rules_doubles", "Парные турниры"),
        ("site_usage_rules", "Правила пользования сайтом"),
    ]
    for slug, title in sections:
        RulesSection.objects.get_or_create(slug=slug, defaults={"title": title, "body": ""})


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0008_rulessection"),
    ]

    operations = [
        migrations.RunPython(create_rules_sections, noop),
    ]
