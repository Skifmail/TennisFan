# Обновить тексты правил: авто-Walkover обоим при дедлайне.

from django.db import migrations

from apps.content.rules_defaults import get_default_rules_body

RULES_SLUGS_TO_REFRESH = (
    "rules_fan",
    "rules_round_robin",
    "rules_olympic",
    "rules_tvd",
    "rating_rules",
)


def refresh_walkover_rules(apps, schema_editor):
    """Перезаписать разделы правил актуальным HTML из шаблонов."""
    RulesSection = apps.get_model("content", "RulesSection")
    for slug in RULES_SLUGS_TO_REFRESH:
        body = get_default_rules_body(slug)
        if not body:
            continue
        section = RulesSection.objects.filter(slug=slug).first()
        if section is None:
            continue
        section.body = body
        section.save(update_fields=["body", "updated_at"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0023_refresh_walkover_neyavka_wording"),
    ]

    operations = [
        migrations.RunPython(refresh_walkover_rules, noop),
    ]
