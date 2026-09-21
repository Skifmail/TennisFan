"""Заменить диагональные зоны Москвы на районы — стороны света.

Юго-Восток → Юг, Юго-Запад → Запад, Северо-Восток → Восток,
Северо-Запад → Север. Идентификаторы строк сохраняются, поэтому турниры
и корты остаются привязанными к тем же записям. Старые названия остаются
в псевдонимах, чтобы распознавать уже созданные турниры.
"""

from django.db import migrations

#: old_slug, new_slug, name, aliases, sort_order
MOSCOW_DISTRICTS: tuple[tuple[str, str, str, str, int], ...] = (
    (
        "severo-zapad",
        "sever",
        "Север",
        "Северный\nСеверное\nСАО\nСеверо-Запад\nСеверо-Западный\nСеверо-Западное\nСЗАО",
        10,
    ),
    (
        "yugo-vostok",
        "yug",
        "Юг",
        "Южный\nЮжное\nЮАО\nЮго-Восток\nЮго-Восточный\nЮго-Восточное\nЮВАО",
        20,
    ),
    (
        "severo-vostok",
        "vostok",
        "Восток",
        "Восточный\nВосточное\nВАО\nСеверо-Восток\nСеверо-Восточный\nСеверо-Восточное\nСВАО",
        30,
    ),
    (
        "yugo-zapad",
        "zapad",
        "Запад",
        "Западный\nЗападное\nЗАО\nЮго-Запад\nЮго-Западный\nЮго-Западное\nЮЗАО",
        40,
    ),
)

#: Для отката: new_slug → исходные значения из 0031_seed_geo_areas.
MOSCOW_DISTRICTS_REVERSE: tuple[tuple[str, str, str, str, int], ...] = (
    (
        "yug",
        "yugo-vostok",
        "Юго-Восток",
        "Юго-Восточный\nЮго-Восточное\nЮВАО",
        10,
    ),
    (
        "zapad",
        "yugo-zapad",
        "Юго-Запад",
        "Юго-Западный\nЮго-Западное\nЮЗАО",
        20,
    ),
    (
        "vostok",
        "severo-vostok",
        "Северо-Восток",
        "Северо-Восточный\nСеверо-Восточное\nСВАО",
        30,
    ),
    (
        "sever",
        "severo-zapad",
        "Северо-Запад",
        "Северо-Западный\nСеверо-Западное\nСЗАО",
        40,
    ),
)


def rename_moscow_districts(apps, schema_editor) -> None:
    """Переименовать четыре московские площадки в стороны света.

    Args:
        apps: Реестр моделей на момент миграции.
        schema_editor: Редактор схемы (не используется).
    """
    GeoArea = apps.get_model("core", "GeoArea")
    for old_slug, new_slug, name, aliases, sort_order in MOSCOW_DISTRICTS:
        GeoArea.objects.filter(slug=old_slug).update(
            slug=new_slug,
            name=name,
            aliases=aliases,
            sort_order=sort_order,
        )


def restore_diagonal_zones(apps, schema_editor) -> None:
    """Вернуть диагональные зоны Москвы.

    Args:
        apps: Реестр моделей на момент миграции.
        schema_editor: Редактор схемы (не используется).
    """
    GeoArea = apps.get_model("core", "GeoArea")
    for new_slug, old_slug, name, aliases, sort_order in MOSCOW_DISTRICTS_REVERSE:
        GeoArea.objects.filter(slug=new_slug).update(
            slug=old_slug,
            name=name,
            aliases=aliases,
            sort_order=sort_order,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0032_locality_settlement_types"),
    ]

    operations = [
        migrations.RunPython(rename_moscow_districts, restore_diagonal_zones),
    ]
