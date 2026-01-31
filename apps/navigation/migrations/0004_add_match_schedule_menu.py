# Migration: add Match Schedule menu item (restored for migration graph consistency)

from django.db import migrations


def add_match_schedule_menu(apps, schema_editor):
    """Добавить пункт меню «Расписание матчей»."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.get_or_create(
        url="/tournaments/schedule/",
        defaults={
            "title": "Расписание матчей",
            "order": 14,
            "is_active": True,
        },
    )


def remove_match_schedule_menu(apps, schema_editor):
    """Удалить пункт меню «Расписание матчей»."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.filter(url="/tournaments/schedule/").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("navigation", "0003_add_tournament_tables_menu"),
    ]

    operations = [
        migrations.RunPython(add_match_schedule_menu, remove_match_schedule_menu),
    ]
