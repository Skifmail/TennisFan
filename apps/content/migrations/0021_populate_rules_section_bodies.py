# Data migration: заполнить пустые RulesSection.body из шаблонов-фолбэков

from django.db import migrations

from apps.content.rules_defaults import (
    RULES_SECTION_TEMPLATES,
    RULES_SECTION_TITLES,
    get_default_rules_body,
)


def populate_rules_bodies(apps, schema_editor):
    """
    Создать недостающие разделы и заполнить пустой body текстом из шаблонов.

    Уже заполненные разделы (например, tennis_rules с правками в админке)
    не перезаписываются.
    """
    RulesSection = apps.get_model("content", "RulesSection")
    for slug in RULES_SECTION_TEMPLATES:
        title = RULES_SECTION_TITLES.get(slug, slug)
        body = get_default_rules_body(slug)
        section, created = RulesSection.objects.get_or_create(
            slug=slug,
            defaults={"title": title, "body": body},
        )
        if created:
            continue
        if (section.body or "").strip():
            continue
        if not body:
            continue
        section.body = body
        section.save(update_fields=["body", "updated_at"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0020_add_rules_tvd_section"),
    ]

    operations = [
        migrations.RunPython(populate_rules_bodies, noop),
    ]
