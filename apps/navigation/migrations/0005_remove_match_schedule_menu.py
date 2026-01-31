# Migration: remove Match Schedule menu item

from django.db import migrations


def remove_match_schedule_menu(apps, schema_editor):
    """Удалить пункт меню «Расписание матчей»."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.filter(url="/tournaments/schedule/").delete()


def add_match_schedule_menu(apps, schema_editor):
    """Восстановить пункт меню (для отката миграции)."""
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItem.objects.get_or_create(
        url="/tournaments/schedule/",
        defaults={
            "title": "Расписание матчей",
            "order": 14,
            "is_active": True,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ("navigation", "0004_add_match_schedule_menu"),
    ]

    operations = [
        migrations.RunPython(remove_match_schedule_menu, add_match_schedule_menu),
    ]
