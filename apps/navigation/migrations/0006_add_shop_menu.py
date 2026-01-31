# Migration: add Shop menu item (enable/disable in admin: Navigation → Menu items)

from django.db import migrations


def add_shop_menu(apps, schema_editor):
    """Добавить пункт меню «Магазин»."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.get_or_create(
        url="/shop/",
        defaults={
            "title": "Магазин",
            "order": 15,
            "is_active": False,  # По умолчанию выключен — включите в админке
        },
    )


def remove_shop_menu(apps, schema_editor):
    """Удалить пункт меню «Магазин»."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.filter(url="/shop/").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("navigation", "0005_remove_match_schedule_menu"),
    ]

    operations = [
        migrations.RunPython(add_shop_menu, remove_shop_menu),
    ]
