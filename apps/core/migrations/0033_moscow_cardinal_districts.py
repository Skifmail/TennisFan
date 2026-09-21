"""Заменить диагональные зоны Москвы на районы — стороны света.

Юго-Восток → Юг, Юго-Запад → Запад, Северо-Восток → Восток,
Северо-Запад → Север. Если в админке уже создали сторону света, а старая
диагональная зона осталась, строки сливаются: турниры и корты переезжают
на каноническую запись, дубль удаляется.
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

_MOSCOW = "moscow"


def _merge_aliases(*blobs: str) -> str:
    """Собрать уникальные псевдонимы, сохраняя порядок появления.

    Args:
        blobs: Тексты псевдонимов, по одному в строке.

    Returns:
        str: Объединённый список без повторов.
    """
    seen: list[str] = []
    seen_norm: set[str] = set()
    for blob in blobs:
        for line in (blob or "").splitlines():
            item = line.strip()
            if not item:
                continue
            key = item.casefold()
            if key in seen_norm:
                continue
            seen_norm.add(key)
            seen.append(item)
    return "\n".join(seen)


def _repoint_geo_area(apps, source_id: int, target_id: int) -> None:
    """Перенести турниры и корты со старой площадки на каноническую.

    Args:
        apps: Реестр моделей на момент миграции.
        source_id: pk удаляемой записи.
        target_id: pk записи, которая останется.
    """
    Tournament = apps.get_model("tournaments", "Tournament")
    Court = apps.get_model("courts", "Court")
    Tournament.objects.filter(geo_area_id=source_id).update(geo_area_id=target_id)
    Court.objects.filter(geo_area_id=source_id).update(geo_area_id=target_id)


def _canonical_district(
    apps, old_slug: str, new_slug: str, name: str, aliases: str, sort_order: int
) -> None:
    """Свести старую зону и уже созданный район к одной записи.

    Args:
        apps: Реестр моделей на момент миграции.
        old_slug: Слаг диагональной зоны.
        new_slug: Канонический слаг стороны света.
        name: Каноническое название.
        aliases: Псевдонимы, которые должны остаться.
        sort_order: Порядок в фильтрах.
    """
    GeoArea = apps.get_model("core", "GeoArea")
    candidates = []
    seen_pks: set[int] = set()
    for lookup in (
        {"slug": new_slug},
        {"slug": old_slug},
        {"region": _MOSCOW, "name": name},
    ):
        row = GeoArea.objects.filter(**lookup).first()
        if row is not None and row.pk not in seen_pks:
            candidates.append(row)
            seen_pks.add(row.pk)

    if not candidates:
        GeoArea.objects.create(
            region=_MOSCOW,
            slug=new_slug,
            name=name,
            aliases=aliases,
            sort_order=sort_order,
            is_active=True,
            is_advertised=True,
        )
        return

    survivor = candidates[0]
    merged_aliases = _merge_aliases(survivor.aliases, aliases, survivor.name)
    for extra in candidates[1:]:
        _repoint_geo_area(apps, extra.pk, survivor.pk)
        merged_aliases = _merge_aliases(merged_aliases, extra.aliases, extra.name)
        extra.delete()

    survivor.region = _MOSCOW
    survivor.slug = new_slug
    survivor.name = name
    survivor.aliases = merged_aliases
    survivor.sort_order = sort_order
    survivor.is_active = True
    survivor.is_advertised = True
    survivor.save()


def rename_moscow_districts(apps, schema_editor) -> None:
    """Переименовать четыре московские площадки в стороны света.

    Args:
        apps: Реестр моделей на момент миграции.
        schema_editor: Редактор схемы (не используется).
    """
    for old_slug, new_slug, name, aliases, sort_order in MOSCOW_DISTRICTS:
        _canonical_district(apps, old_slug, new_slug, name, aliases, sort_order)


def restore_diagonal_zones(apps, schema_editor) -> None:
    """Вернуть диагональные зоны Москвы, если слаг ещё свободен.

    Args:
        apps: Реестр моделей на момент миграции.
        schema_editor: Редактор схемы (не используется).
    """
    GeoArea = apps.get_model("core", "GeoArea")
    for new_slug, old_slug, name, aliases, sort_order in MOSCOW_DISTRICTS_REVERSE:
        if GeoArea.objects.filter(slug=old_slug).exists():
            continue
        GeoArea.objects.filter(slug=new_slug).update(
            slug=old_slug,
            name=name,
            aliases=aliases,
            sort_order=sort_order,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0032_locality_settlement_types"),
        ("courts", "0014_court_geo_area_court_region"),
        ("tournaments", "0056_tournament_geo_area_tournament_region"),
    ]

    operations = [
        migrations.RunPython(rename_moscow_districts, restore_diagonal_zones),
    ]
