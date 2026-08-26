"""Наполнение справочника зон Москвы и городов Московской области."""

from django.db import migrations

#: Начальный справочник: регион, название, слаг, псевдонимы, порядок, реклама.
GEO_AREAS: tuple[tuple[str, str, str, str, int, bool], ...] = (
    (
        "moscow",
        "Юго-Восток",
        "yugo-vostok",
        "Юго-Восточный\nЮго-Восточное\nЮВАО",
        10,
        True,
    ),
    (
        "moscow",
        "Юго-Запад",
        "yugo-zapad",
        "Юго-Западный\nЮго-Западное\nЮЗАО",
        20,
        True,
    ),
    (
        "moscow",
        "Северо-Восток",
        "severo-vostok",
        "Северо-Восточный\nСеверо-Восточное\nСВАО",
        30,
        True,
    ),
    (
        "moscow",
        "Северо-Запад",
        "severo-zapad",
        "Северо-Западный\nСеверо-Западное\nСЗАО",
        40,
        True,
    ),
    ("moscow_oblast", "Раменское", "ramenskoe", "Раменский", 10, False),
    ("moscow_oblast", "Жуковский", "zhukovskiy", "Жуковском", 20, False),
    ("moscow_oblast", "Воскресенск", "voskresensk", "Воскресенске", 30, False),
    (
        "moscow_oblast",
        "Павловский Посад",
        "pavlovskiy-posad",
        "Павловском Посаде",
        40,
        False,
    ),
)


def seed_geo_areas(apps, schema_editor) -> None:
    """Создать зоны и города, не затрагивая уже существующие записи.

    Args:
        apps: Реестр моделей на момент миграции.
        schema_editor: Редактор схемы (не используется).
    """
    GeoArea = apps.get_model("core", "GeoArea")
    for region, name, slug, aliases, sort_order, is_advertised in GEO_AREAS:
        GeoArea.objects.update_or_create(
            slug=slug,
            defaults={
                "region": region,
                "name": name,
                "aliases": aliases,
                "sort_order": sort_order,
                "is_active": True,
                "is_advertised": is_advertised,
            },
        )


def remove_geo_areas(apps, schema_editor) -> None:
    """Удалить записи, созданные этой миграцией.

    Args:
        apps: Реестр моделей на момент миграции.
        schema_editor: Редактор схемы (не используется).
    """
    GeoArea = apps.get_model("core", "GeoArea")
    GeoArea.objects.filter(slug__in=[item[2] for item in GEO_AREAS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0030_geoarea"),
    ]

    operations = [
        migrations.RunPython(seed_geo_areas, remove_geo_areas),
    ]
