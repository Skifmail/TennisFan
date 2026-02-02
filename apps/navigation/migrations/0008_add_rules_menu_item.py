# Generated manually: add Rules menu item back

from django.db import migrations


def add_rules_menu_item(apps, schema_editor):
    """Добавить пункт меню «Правила» (url=/rules/)."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.get_or_create(
        url="/rules/",
        defaults={
            "title": "Правила",
            "order": 8,
            "is_active": True,
        },
    )


def remove_rules_menu_item(apps, schema_editor):
    """Удалить пункт меню «Правила»."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.filter(url="/rules/").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("navigation", "0007_remove_rules_menu_item"),
    ]

    operations = [
        migrations.RunPython(add_rules_menu_item, remove_rules_menu_item),
    ]
