# Generated manually: remove rules page from database

from django.db import migrations


def remove_rules_page(apps, schema_editor):
    """Удалить страницу rules из базы данных."""
    Page = apps.get_model("content", "Page")
    Page.objects.filter(slug="rules").delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0006_add_contact_item_fk"),
    ]

    operations = [
        migrations.RunPython(remove_rules_page, noop),
    ]
