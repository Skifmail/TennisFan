# Generated migration: add Tournament Tables menu item

from django.db import migrations


def add_tournament_tables_menu(apps, schema_editor):
    """Добавить пункт меню «Турнирные таблицы» (подключаемый/отключаемый в админке)."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.get_or_create(
        url="/tournaments/tables/",
        defaults={
            "title": "Турнирные таблицы",
            "order": 2,
            "is_active": True,
        },
    )


def remove_tournament_tables_menu(apps, schema_editor):
    """Удалить пункт меню «Турнирные таблицы»."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.filter(url="/tournaments/tables/").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("navigation", "0002_seed_menu_items"),
    ]

    operations = [
        migrations.RunPython(add_tournament_tables_menu, remove_tournament_tables_menu),
    ]
