"""География публичной страницы тренировок.

Базовый набор — Москва и города области из справочника ``GeoArea``.
Список открывается в Москве с чипами районов. Другой город в поле
«Город» скрывает зоны Москвы и оставляет тренировки этого города.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import QuerySet
from django.utils.text import slugify

from apps.core.geo import GeoRegion, canonical_area_slug, normalize_geo_text
from apps.core.models import GeoArea
from apps.courts.models import Court

MOSCOW_CITY = "Москва"
OTHER_CITIES_LABEL = "Другие города"
OTHER_CITIES_REGION = "other"


def is_moscow_city(name: str) -> bool:
    """Проверить, что название города — Москва."""
    return bool(name) and normalize_geo_text(name) == normalize_geo_text(MOSCOW_CITY)


def oblast_area_for_city(
    city: str, areas: list[GeoArea] | None = None
) -> GeoArea | None:
    """Найти район области по названию города, если он есть в каталоге."""
    needle = normalize_geo_text(city)
    if not needle:
        return None
    catalog = areas if areas is not None else advertised_training_areas()
    for area in catalog:
        if area.region == GeoRegion.MOSCOW_OBLAST and needle in area.get_alias_list():
            return area
    return None


@dataclass(frozen=True)
class TrainingCityGroup:
    """Город рекламируемой географии и активные корты в нём."""

    city: str
    courts: tuple[Court, ...]


@dataclass(frozen=True)
class TrainingPlace:
    """Выбранный район справочника или город активной тренировки."""

    slug: str
    name: str


@dataclass(frozen=True)
class TrainingListGeo:
    """Город публичного списка и район Москвы, если он выбран."""

    city: str
    moscow_district: GeoArea | None
    show_moscow_zones: bool


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
    """Активные корты для формы записи: справочник и города тренировок.

    Returns:
        QuerySet[Court]: Корты по городу и названию.
    """
    pks = [court.pk for group in group_training_courts() for court in group.courts]
    for city in extra_training_cities():
        pks.extend(court.pk for court in courts_for_extra_city(city))
    return Court.objects.filter(pk__in=pks).order_by("city", "name")


def _catalog_city_needles(areas: list[GeoArea]) -> set[str]:
    """Нормализованные названия и псевдонимы справочника географии."""
    needles = {normalize_geo_text(MOSCOW_CITY)}
    for area in areas:
        needles.update(area.get_alias_list())
    return needles


def extra_training_cities(areas: list[GeoArea] | None = None) -> list[str]:
    """Города активных тренировок, которых нет в справочнике Москвы и области.

    Args:
        areas: Кэш справочника. Если не передан, читается из базы.

    Returns:
        list[str]: Уникальные названия по алфавиту, как их указали в тренировках.
    """
    from apps.training.models import Training

    catalog = areas if areas is not None else advertised_training_areas()
    needles = _catalog_city_needles(catalog)
    display_by_key: dict[str, str] = {}
    cities = (
        Training.objects.filter(is_active=True)
        .exclude(city="")
        .values_list("city", flat=True)
    )
    for raw in cities:
        _remember_extra_city(display_by_key, raw, needles)
    return sorted(display_by_key.values(), key=lambda name: name.casefold())


def extra_place_cities(areas: list[GeoArea] | None = None) -> list[str]:
    """Города активных тренировок вне справочника.

    Совпадает с ``extra_training_cities``: в фильтр не попадают корты
    без тренировки, иначе страница снова превращается в дамп площадок.

    Args:
        areas: Кэш справочника. Если не передан, читается из базы.

    Returns:
        list[str]: Уникальные названия по алфавиту.
    """
    return extra_training_cities(areas)


def _remember_extra_city(
    display_by_key: dict[str, str],
    raw: str,
    needles: set[str],
) -> None:
    """Запомнить город, если его нет в справочнике Москвы и области."""
    name = (raw or "").strip()
    key = normalize_geo_text(name)
    if not key or key in needles:
        return
    display_by_key.setdefault(key, name)


def public_training_cities(areas: list[GeoArea] | None = None) -> list[str]:
    """Города для заголовка: справочник, затем города активных тренировок.

    Args:
        areas: Кэш справочника. Если не передан, читается из базы.

    Returns:
        list[str]: Москва, область и дополнительные города тренировок.
    """
    catalog = areas if areas is not None else advertised_training_areas()
    return advertised_training_cities() + extra_training_cities(catalog)


def public_training_cities_label(areas: list[GeoArea] | None = None) -> str:
    """Подзаголовок страницы: справочник и, если нужно, другие города.

    Один дополнительный город называется прямо. Несколько не перечисляем,
    чтобы шапка не превращалась в список площадок.

    Args:
        areas: Кэш справочника. Если не передан, читается из базы.

    Returns:
        str: Перечень для подзаголовка.
    """
    catalog = areas if areas is not None else advertised_training_areas()
    advertised = advertised_training_cities()
    extras = extra_training_cities(catalog)
    if not extras:
        return format_city_list(advertised)
    if len(extras) == 1:
        return format_city_list([*advertised, extras[0]])
    return f"{', '.join(advertised)} и другие города"


def training_city_slug(city: str) -> str:
    """Слаг города тренировки для query-параметра ``area``.

    Args:
        city: Название населённого пункта.

    Returns:
        str: Unicode-слаг либо ``city``, если название не даёт слага.
    """
    return slugify(city, allow_unicode=True) or "city"


def courts_for_extra_city(city: str) -> tuple[Court, ...]:
    """Активные корты выбранного города вне справочника.

    Args:
        city: Название города тренировки.

    Returns:
        tuple[Court, ...]: Корты с тем же нормализованным городом.
    """
    needle = normalize_geo_text(city)
    if not needle:
        return ()
    matched = [
        court
        for court in Court.objects.filter(is_active=True).order_by("name")
        if normalize_geo_text(court.city) == needle
    ]
    return tuple(matched)


def filter_trainings_by_city(queryset: QuerySet, city: str) -> QuerySet:
    """Оставить тренировки выбранного города.

    Args:
        queryset: Уже отфильтрованный список тренировок.
        city: Название города.

    Returns:
        QuerySet: Тренировки с тем же нормализованным городом.
    """
    needle = normalize_geo_text(city)
    if not needle:
        return queryset.none()
    matched_pks = [
        training.pk
        for training in queryset
        if normalize_geo_text(training.city) == needle
    ]
    if not matched_pks:
        return queryset.none()
    return queryset.filter(pk__in=matched_pks)


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
    """Вернуть активные корты выбранного района, города области или другого города.

    Args:
        area_slug: Слаг ``GeoArea`` или слаг города активной тренировки.
            Пустой или неизвестный слаг даёт пустой список. Слаг справочника
            важнее, если совпадёт со слагом дополнительного города.
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
        extra_city = next(
            (
                city
                for city in extra_training_cities(catalog)
                if training_city_slug(city) == needle
            ),
            None,
        )
        if extra_city is None:
            return ()
        return courts_for_extra_city(extra_city)

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


def resolve_training_list_geo(
    city: str = "",
    area_slug: str = "",
    areas: list[GeoArea] | None = None,
) -> TrainingListGeo:
    """Разобрать фильтр списка: Москва по умолчанию, район только внутри неё.

    Старый ``?area=ramenskoe`` без ``city`` превращается в город Раменское.
    Неизвестный ``area`` без города не уводит со Москвы.

    Args:
        city: Текст из поля «Город».
        area_slug: Слаг района или устаревший слаг площадки.
        areas: Кэш справочника. Если не передан, читается из базы.

    Returns:
        TrainingListGeo: Город, район Москвы и флаг чипов зон.
    """
    catalog = areas if areas is not None else advertised_training_areas()
    moscow = _moscow_areas(catalog)
    oblast = _oblast_areas_from(catalog)
    needle = canonical_area_slug((area_slug or "").strip().lower())
    typed = (city or "").strip()

    if typed:
        if is_moscow_city(typed):
            district = next((area for area in moscow if area.slug == needle), None)
            return TrainingListGeo(
                city=MOSCOW_CITY,
                moscow_district=district,
                show_moscow_zones=True,
            )
        return TrainingListGeo(
            city=typed,
            moscow_district=None,
            show_moscow_zones=False,
        )

    if needle:
        district = next((area for area in moscow if area.slug == needle), None)
        if district is not None:
            return TrainingListGeo(
                city=MOSCOW_CITY,
                moscow_district=district,
                show_moscow_zones=True,
            )
        oblast_match = next((area for area in oblast if area.slug == needle), None)
        if oblast_match is not None:
            return TrainingListGeo(
                city=oblast_match.name,
                moscow_district=None,
                show_moscow_zones=False,
            )
        extra = next(
            (
                name
                for name in extra_training_cities(catalog)
                if training_city_slug(name) == needle
            ),
            "",
        )
        if extra:
            return TrainingListGeo(
                city=extra,
                moscow_district=None,
                show_moscow_zones=False,
            )

    return TrainingListGeo(
        city=MOSCOW_CITY,
        moscow_district=None,
        show_moscow_zones=True,
    )


def courts_for_training_city(
    city: str,
    areas: list[GeoArea] | None = None,
) -> tuple[Court, ...]:
    """Корты выбранного города: справочник области или свободный город.

    Args:
        city: Название города из фильтра.
        areas: Кэш справочника. Если не передан, читается из базы.

    Returns:
        tuple[Court, ...]: Корты города по названию.
    """
    catalog = areas if areas is not None else advertised_training_areas()
    oblast = oblast_area_for_city(city, catalog)
    if oblast is not None:
        return courts_for_training_area(oblast.slug, catalog)
    return courts_for_extra_city(city)
