"""Рекламируемая география тренировок: Москва и города области.

Заголовок страницы называет города. Выбор площадок идёт по районам Москвы
и городам области из справочника ``GeoArea``.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import QuerySet

from apps.core.geo import GeoRegion, canonical_area_slug, normalize_geo_text
from apps.core.models import GeoArea
from apps.courts.models import Court

MOSCOW_CITY = "Москва"


@dataclass(frozen=True)
class TrainingCityGroup:
    """Город рекламируемой географии и активные корты в нём."""

    city: str
    courts: tuple[Court, ...]


def advertised_training_cities() -> list[str]:
    """Вернуть города, которые рекламируем на странице тренировок.

    Returns:
        list[str]: Москва, затем активные города Московской области
        в порядке справочника.
    """
    cities: list[str] = [MOSCOW_CITY]
    oblast_names = (
        GeoArea.objects.filter(
            region=GeoRegion.MOSCOW_OBLAST,
            is_active=True,
        )
        .order_by("sort_order", "name")
        .values_list("name", flat=True)
    )
    cities.extend(oblast_names)
    return cities


def format_city_list(cities: list[str]) -> str:
    """Собрать перечень городов для заголовка и лида.

    Args:
        cities: Названия городов в именительном падеже.

    Returns:
        str: Один город как есть, несколько через запятую и «и» перед последним.
    """
    names = [name for name in cities if name]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} и {names[-1]}"


def _oblast_areas() -> list[GeoArea]:
    """Активные города области из справочника."""
    return list(
        GeoArea.objects.filter(
            region=GeoRegion.MOSCOW_OBLAST,
            is_active=True,
        ).order_by("sort_order", "name")
    )


def training_city_for_court(
    court: Court,
    oblast_areas: list[GeoArea] | None = None,
) -> str | None:
    """Определить рекламируемый город корта или вернуть None.

    Args:
        court: Площадка.
        oblast_areas: Кэш городов области. Если не передан, читается из базы.

    Returns:
        str | None: Название города из рекламируемого набора либо None.
    """
    areas = oblast_areas if oblast_areas is not None else _oblast_areas()
    if court.geo_area_id:
        area = court.geo_area
        if area.region == GeoRegion.MOSCOW_OBLAST:
            return str(area.name)
        if area.region == GeoRegion.MOSCOW:
            return MOSCOW_CITY

    city_n = normalize_geo_text(court.city)
    for area in areas:
        if city_n in area.get_alias_list():
            return str(area.name)

    if court.region == GeoRegion.MOSCOW or city_n == normalize_geo_text(MOSCOW_CITY):
        return MOSCOW_CITY
    return None


def group_training_courts(city_filter: str = "") -> list[TrainingCityGroup]:
    """Сгруппировать активные корты по рекламируемым городам.

    Город без кортов остаётся в списке: иначе заголовок и перечень
    площадок снова разъедутся.

    Args:
        city_filter: Если задан, оставить только совпадающий город.

    Returns:
        list[TrainingCityGroup]: Города в порядке справочника.
    """
    cities = advertised_training_cities()
    needle = normalize_geo_text(city_filter)
    if needle:
        cities = [city for city in cities if needle in normalize_geo_text(city)]

    areas = _oblast_areas()
    buckets: dict[str, list[Court]] = {city: [] for city in cities}
    courts = (
        Court.objects.filter(is_active=True).select_related("geo_area").order_by("name")
    )
    for court in courts:
        city = training_city_for_court(court, areas)
        if city in buckets:
            buckets[city].append(court)

    return [
        TrainingCityGroup(city=city, courts=tuple(buckets[city])) for city in cities
    ]


def advertised_training_courts() -> QuerySet[Court]:
    """Активные корты в рекламируемых городах для формы записи.

    Returns:
        QuerySet[Court]: Корты по городу и названию.
    """
    pks = [court.pk for group in group_training_courts() for court in group.courts]
    return Court.objects.filter(pk__in=pks).order_by("city", "name")


def advertised_training_areas() -> list[GeoArea]:
    """Вернуть районы Москвы и города области для выбора на тренировках.

    Returns:
        list[GeoArea]: Сначала районы Москвы, затем города области.
    """
    return list(
        GeoArea.objects.filter(is_active=True).order_by("region", "sort_order", "name")
    )


def _moscow_areas(areas: list[GeoArea]) -> list[GeoArea]:
    """Районы Москвы из переданного набора."""
    return [area for area in areas if area.region == GeoRegion.MOSCOW]


def _oblast_areas_from(areas: list[GeoArea]) -> list[GeoArea]:
    """Города области из переданного набора."""
    return [area for area in areas if area.region == GeoRegion.MOSCOW_OBLAST]


def _match_area_by_text(text: str, areas: list[GeoArea]) -> GeoArea | None:
    """Найти площадку по точному названию или псевдониму.

    Args:
        text: Город или район корта.
        areas: Кандидаты из справочника.

    Returns:
        GeoArea | None: Совпадение по нормализованному имени либо None.
    """
    haystack = normalize_geo_text(text)
    if not haystack:
        return None
    for area in areas:
        if haystack in area.get_alias_list():
            return area
    return None


def training_area_for_court(
    court: Court,
    areas: list[GeoArea] | None = None,
) -> GeoArea | None:
    """Определить район или город корта из рекламируемого набора.

    Args:
        court: Площадка.
        areas: Кэш справочника. Если не передан, читается из базы.

    Returns:
        GeoArea | None: Район Москвы или город области, либо None.
    """
    catalog = areas if areas is not None else advertised_training_areas()
    if court.geo_area_id:
        for area in catalog:
            if area.pk == court.geo_area_id:
                return area

    if court.region == GeoRegion.MOSCOW:
        return _match_area_by_text(court.district, _moscow_areas(catalog))

    oblast_match = _match_area_by_text(court.city, _oblast_areas_from(catalog))
    if oblast_match is not None:
        return oblast_match
    if court.region == GeoRegion.MOSCOW_OBLAST:
        return None
    return _match_area_by_text(court.district, _moscow_areas(catalog))


def courts_for_training_area(
    area_slug: str,
    areas: list[GeoArea] | None = None,
) -> tuple[Court, ...]:
    """Вернуть активные корты выбранного района или города.

    Args:
        area_slug: Слаг ``GeoArea``. Пустой или неизвестный слаг даёт пустой список.
        areas: Кэш справочника. Если не передан, читается из базы.

    Returns:
        tuple[Court, ...]: Корты выбранной площадки по названию.
    """
    needle = canonical_area_slug((area_slug or "").strip().lower())
    if not needle:
        return ()
    catalog = areas if areas is not None else advertised_training_areas()
    selected = next((area for area in catalog if area.slug == needle), None)
    if selected is None:
        return ()

    with_fk = list(
        Court.objects.filter(is_active=True, geo_area=selected).order_by("name")
    )
    seen = {court.pk for court in with_fk}
    fallback: list[Court] = []
    unbound = Court.objects.filter(is_active=True, geo_area__isnull=True).order_by(
        "name"
    )
    for court in unbound:
        area = training_area_for_court(court, catalog)
        if area is not None and area.pk == selected.pk and court.pk not in seen:
            fallback.append(court)
            seen.add(court.pk)

    matched = with_fk + fallback
    matched.sort(key=lambda court: court.name.casefold())
    return tuple(matched)
