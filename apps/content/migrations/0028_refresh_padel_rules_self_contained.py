"""Обновить правила падела: сроки, Walkover и результат описаны полностью."""

from django.db import migrations

from apps.content.rules_defaults import get_default_rules_body


def refresh_padel_rules(apps, schema_editor):
    """Перезаписать раздел padel_rules самодостаточным текстом."""
    RulesSection = apps.get_model("content", "RulesSection")
    body = get_default_rules_body("padel_rules")
    if not body:
        return
    section = RulesSection.objects.filter(slug="padel_rules").first()
    if section is None:
        RulesSection.objects.create(
            slug="padel_rules",
            title="Правила падела",
            body=body,
        )
        return
    section.body = body
    section.save(update_fields=["body", "updated_at"])


def noop(apps, schema_editor):
    """Откат не восстанавливает отсылку к другим правилам."""
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0027_refresh_padel_rules"),
    ]

    operations = [
        migrations.RunPython(refresh_padel_rules, noop),
    ]
