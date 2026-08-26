"""Рекламируемая география тренировок: Москва и города области.

Список городов берётся из справочника ``GeoArea``, чтобы маркетинг мог
добавлять и скрывать направления без правки шаблонов. Заголовок, текст
и список кортов на публичной странице используют один и тот же набор.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import QuerySet

from apps.core.geo import GeoRegion, normalize_geo_text
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
