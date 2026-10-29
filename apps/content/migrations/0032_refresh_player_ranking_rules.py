"""Обновить правила: публичный рейтинг считается по победам, матчам и % побед."""

from django.db import migrations

from apps.content.rules_defaults import get_default_rules_body

SLUGS = ("rating_rules", "rules_rating_algorithm")


def refresh_ranking_rules(apps, schema_editor):
    """Перезаписать разделы рейтинга актуальной формулой из шаблонов."""
    RulesSection = apps.get_model("content", "RulesSection")
    for slug in SLUGS:
        body = get_default_rules_body(slug)
        if not body:
            continue
        section = RulesSection.objects.filter(slug=slug).first()
        if section is None:
            continue
        section.body = body
        section.save(update_fields=["body", "updated_at"])


def noop(apps, schema_editor):
    """Откат не возвращает прежний текст правил."""
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0031_alter_stringerapplication_applicant"),
    ]

    operations = [
        migrations.RunPython(refresh_ranking_rules, noop),
    ]
