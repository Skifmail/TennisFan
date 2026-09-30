"""Пункт меню «Падел» ведёт в каталог турниров этого вида спорта."""

from django.db import migrations


def add_padel_menu_item(apps, schema_editor):
    """Добавить пункт меню падела."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.get_or_create(
        url="/tournaments/?sport=padel",
        defaults={"title": "Падел", "order": 3, "is_active": True},
    )


def remove_padel_menu_item(apps, schema_editor):
    """Удалить пункт меню падела."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.filter(url="/tournaments/?sport=padel").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0009_add_stringers_menu_item"),
    ]

    operations = [
        migrations.RunPython(add_padel_menu_item, remove_padel_menu_item),
    ]
