"""Обновить текст правил: сила показывается до сотых без округления вверх."""

from django.db import migrations

from apps.content.rules_defaults import get_default_rules_body


def refresh_rating_algorithm_rules(apps, schema_editor):
    """Перезаписать раздел алгоритма рейтинга из шаблона."""
    RulesSection = apps.get_model("content", "RulesSection")
    body = get_default_rules_body("rules_rating_algorithm")
    if not body:
        return
    section = RulesSection.objects.filter(slug="rules_rating_algorithm").first()
    if section is None:
        return
    section.body = body
    section.save(update_fields=["body", "updated_at"])


def noop(apps, schema_editor):
    """Откат не возвращает прежний текст правил."""
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0024_refresh_dual_walkover_rules"),
    ]

    operations = [
        migrations.RunPython(refresh_rating_algorithm_rules, noop),
    ]
