# Generated manually: remove rules menu item from database

from django.db import migrations


def remove_rules_menu_item(apps, schema_editor):
    """Удалить пункт меню «Правила» (url=/rules/)."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.filter(url="/rules/").delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("navigation", "0006_add_shop_menu"),
    ]

    operations = [
        migrations.RunPython(remove_rules_menu_item, noop),
    ]
