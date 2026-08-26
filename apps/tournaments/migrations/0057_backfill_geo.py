"""Заполнение региона и зоны/города у существующих турниров и кортов.

Зона московских турниров зашита в название («Любительский Юго-Восточный кубок
Москвы»), город подмосковных — в поле ``city``. Миграция только дописывает
пустые поля: состав турниров не меняется, ничего не удаляется.
"""

from django.db import migrations

#: Символы, встречающиеся в названиях вместо обычного дефиса.
_HYPHENS = "\u2010\u2011\u2012\u2013\u2014\u2212"

MOSCOW = "moscow"
MOSCOW_OBLAST = "moscow_oblast"


def _normalize(text: str) -> str:
    """Привести текст к сравнимому виду.

    Args:
        text: Название турнира, города или псевдоним площадки.

    Returns:
        str: Нижний регистр, обычные дефисы, одиночные пробелы.
    """
    normalized = (text or "").strip().lower()
    for hyphen in _HYPHENS:
        normalized = normalized.replace(hyphen, "-")
    return " ".join(normalized.split())


def _build_alias_index(GeoArea) -> list[tuple[str, str, object]]:
    """Собрать индекс «псевдоним → площадка», длинные варианты первыми.

    Args:
        GeoArea: Историческая модель справочника площадок.

    Returns:
        list[tuple[str, str, object]]: Тройки (псевдоним, регион, площадка).
    """
    index: list[tuple[str, str, object]] = []
    for area in GeoArea.objects.filter(is_active=True):
        variants = [area.name, *(area.aliases or "").splitlines()]
        for variant in variants:
            alias = _normalize(variant)
            if alias:
                index.append((alias, area.region, area))
    # «Юго-Восток» должен срабатывать раньше, чем «Восток».
    return sorted(index, key=lambda item: -len(item[0]))


def _match(index, text: str, region: str | None = None):
    """Найти площадку, упомянутую в тексте.

    Args:
        index: Индекс псевдонимов из ``_build_alias_index``.
        text: Текст для разбора.
        region: Ограничить поиск регионом, если он известен.

    Returns:
        object | None: Площадка либо None.
    """
    haystack = _normalize(text)
    if not haystack:
        return None
    for alias, area_region, area in index:
        if region and area_region != region:
            continue
        if alias in haystack:
            return area
    return None


def backfill(apps, schema_editor) -> None:
    """Проставить регион и площадку турнирам и кортам.

    Args:
        apps: Реестр моделей на момент миграции.
        schema_editor: Редактор схемы (не используется).
    """
    GeoArea = apps.get_model("core", "GeoArea")
    Tournament = apps.get_model("tournaments", "Tournament")
    Court = apps.get_model("courts", "Court")

    index = _build_alias_index(GeoArea)
    if not index:
        return

    for court in Court.objects.filter(region="", geo_area__isnull=True):
        area = _match(index, court.city, region=MOSCOW_OBLAST)
        if area is not None:
            court.region = MOSCOW_OBLAST
            court.geo_area = area
        elif "москва" in _normalize(court.city):
            court.region = MOSCOW
            court.geo_area = _match(index, court.district, region=MOSCOW)
        else:
            continue
        court.save(update_fields=["region", "geo_area"])

    for tournament in Tournament.objects.filter(
        region="", geo_area__isnull=True
    ).select_related("court"):
        city = _normalize(tournament.city)

        if "москва" in city or "москв" in _normalize(tournament.name):
            tournament.region = MOSCOW
            tournament.geo_area = _match(index, tournament.name, region=MOSCOW)
        else:
            area = _match(index, tournament.city, region=MOSCOW_OBLAST)
            if area is None and tournament.court_id:
                area = _match(index, tournament.court.city, region=MOSCOW_OBLAST)
            if area is None:
                continue
            tournament.region = MOSCOW_OBLAST
            tournament.geo_area = area

        tournament.save(update_fields=["region", "geo_area"])


def clear(apps, schema_editor) -> None:
    """Очистить заполненные миграцией поля.

    Args:
        apps: Реестр моделей на момент миграции.
        schema_editor: Редактор схемы (не используется).
    """
    apps.get_model("tournaments", "Tournament").objects.update(region="", geo_area=None)
    apps.get_model("courts", "Court").objects.update(region="", geo_area=None)


class Migration(migrations.Migration):
    dependencies = [
        ("tournaments", "0056_tournament_geo_area_tournament_region"),
        ("courts", "0014_court_geo_area_court_region"),
        ("core", "0031_seed_geo_areas"),
    ]

    operations = [
        migrations.RunPython(backfill, clear),
    ]
