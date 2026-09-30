"""Обновить раздел правил падела из шаблона-фолбэка."""

from django.db import migrations

from apps.content.rules_defaults import get_default_rules_body


def refresh_padel_rules(apps, schema_editor):
    """Перезаписать padel_rules текстом из шаблона, как у тенниса."""
    RulesSection = apps.get_model("content", "RulesSection")
    body = get_default_rules_body("padel_rules")
    if not body:
        return
    section, _created = RulesSection.objects.get_or_create(
        slug="padel_rules",
        defaults={"title": "Правила падела", "body": body},
    )
    section.title = "Правила падела"
    section.body = body
    section.save(update_fields=["title", "body", "updated_at"])


def noop(apps, schema_editor):
    """Откат не возвращает черновой текст."""
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0026_add_padel_rules"),
    ]

    operations = [
        migrations.RunPython(refresh_padel_rules, noop),
    ]
