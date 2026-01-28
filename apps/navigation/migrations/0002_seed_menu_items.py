# Generated data migration: seed initial nav menu items

from django.db import migrations


def seed_menu_items(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    items = [
        (1, "Турниры", "/tournaments/"),
        (2, "Рейтинг", "/rating/"),
        (3, "Лига", "/tournaments/champions-league/"),
        (4, "Результаты", "/results/"),
        (5, "Спарринг", "/sparring/"),
        (6, "Тренировки", "/training/"),
        (7, "Зал славы", "/legends/"),
        (8, "Правила", "/rules/"),
        (9, "Корты", "/courts/"),
        (10, "Фото", "/gallery/"),
        (11, "Новости", "/news/"),
    ]
    for order, title, url in items:
        MenuItem.objects.get_or_create(
            url=url,
            defaults={"title": title, "order": order, "is_active": True},
        )


def reverse_seed(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    urls = [
        "/tournaments/",
        "/rating/",
        "/tournaments/champions-league/",
        "/results/",
        "/sparring/",
        "/training/",
        "/legends/",
        "/rules/",
        "/courts/",
        "/gallery/",
        "/news/",
    ]
    MenuItem.objects.filter(url__in=urls).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("navigation", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_menu_items, reverse_seed),
    ]
