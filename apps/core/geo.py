"""География платформы: регионы проведения турниров и тренировок.

Регион — верхний уровень навигации: Москва делится на районы, область — на
города. Перечисление, а не таблица в базе: значений два и они не меняются,
а редактируемый уровень вынесен в модель ``apps.core.models.GeoArea``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.db import models

if TYPE_CHECKING:
    from apps.core.models import GeoArea


class GeoRegion(models.TextChoices):
    """Регион проведения: Москва или Московская область."""

    MOSCOW = "moscow", "Москва"
    MOSCOW_OBLAST = "moscow_oblast", "Московская область"


#: Слаг региона в ЧПУ → значение поля ``region``.
REGION_SLUGS: dict[str, str] = {
    "moscow": "moscow",
    "moskovskaya-oblast": "moscow_oblast",
}

#: Значение поля ``region`` → слаг региона в ЧПУ.
REGION_BY_VALUE: dict[str, str] = {value: slug for slug, value in REGION_SLUGS.items()}

#: Название единицы внутри региона — для заголовков и подписей фильтров.
AREA_LABELS: dict[str, str] = {
    "moscow": "район",
    "moscow_oblast": "город",
}

#: Старые диагональные слаги Москвы → актуальные стороны света.
LEGACY_AREA_SLUGS: dict[str, str] = {
    "yugo-vostok": "yug",
    "yugo-zapad": "zapad",
    "severo-vostok": "vostok",
    "severo-zapad": "sever",
}

MOSCOW_CITY_NAMES = frozenset(
    {
        "москва",
        "moscow",
        "moskva",
    }
)

#: Хвосты, которые автодополнение иногда дописывает к названию города.
_CITY_NAME_SUFFIXES = (", россия", ", рф", " россия", " рф")


#: Символы, которые в названиях встречаются вместо обычного дефиса.
_HYPHENS = "‐‑‒–—−"


def normalize_geo_text(text: str) -> str:
    """Привести текст к виду, пригодному для сравнения названий.

    В названиях турниров встречается неразрывный дефис (U+2011) и другие тире,
    из-за чего «Юго‑Восточный» не совпадал бы с «Юго-Восточный».

    Args:
        text: Произвольный текст: название турнира или псевдоним площадки.

    Returns:
        str: Текст в нижнем регистре с обычными дефисами и без лишних пробелов.
    """
    normalized = (text or "").strip().lower()
    for hyphen in _HYPHENS:
        normalized = normalized.replace(hyphen, "-")
    return " ".join(normalized.split())


def region_from_slug(slug: str) -> str | None:
    """Определить регион по слагу из URL.

    Args:
        slug: Слаг региона, например ``moscow`` или ``moskovskaya-oblast``.

    Returns:
        str | None: Значение поля ``region`` либо None, если слаг неизвестен.
    """
    return REGION_SLUGS.get((slug or "").strip().lower())


def region_to_slug(region: str) -> str:
    """Вернуть слаг региона для построения ЧПУ.

    Args:
        region: Значение поля ``region``.

    Returns:
        str: Слаг для URL либо пустая строка, если регион не задан.
    """
    return REGION_BY_VALUE.get(region or "", "")


def canonicalize_city_name(name: str) -> str:
    """Убрать служебные префиксы и хвосты у названия населённого пункта.

    Args:
        name: Сырое значение поля «Населённый пункт».

    Returns:
        str: Нормализованное имя без «г.» и «, Россия».
    """
    needle = normalize_geo_text(name)
    if needle.startswith("г. "):
        needle = needle[3:]
    elif needle.startswith("г "):
        needle = needle[2:]
    for suffix in _CITY_NAME_SUFFIXES:
        if needle.endswith(suffix):
            needle = needle[: -len(suffix)].rstrip(" ,")
            break
    return needle


def is_moscow_city_name(name: str) -> bool:
    """Проверить, что населённый пункт — Москва, а не другой город России.

    Args:
        name: Название из поля «Населённый пункт».

    Returns:
        bool: True только для Москвы.
    """
    return canonicalize_city_name(name) in MOSCOW_CITY_NAMES


def oblast_geo_area_for_city(city: str) -> GeoArea | None:
    """Найти город области в справочнике по точному названию или псевдониму.

    ``GeoArea.resolve_from_name`` ищет подстроку в произвольном тексте
    («Юг» внутри названия турнира) — для поля «Населённый пункт» это слишком
    широко, поэтому сравниваем нормализованное имя целиком.

    Args:
        city: Название населённого пункта.

    Returns:
        GeoArea | None: Площадка области либо None.
    """
    needle = canonicalize_city_name(city)
    if not needle:
        return None
    from apps.core.models import GeoArea

    for area in GeoArea.objects.filter(region=GeoRegion.MOSCOW_OBLAST, is_active=True):
        typed_area = cast(GeoArea, area)
        if needle in typed_area.get_alias_list():
            return typed_area
    return None


def city_uses_moscow_geo(city: str) -> bool:
    """Нужны ли поля региона и района Москвы для этого города.

    Справочник ``GeoRegion`` / ``GeoArea`` описывает только Москву и область.
    Санкт-Петербург, Казань и остальные города живут в поле ``city``.

    Args:
        city: Название населённого пункта.

    Returns:
        bool: True для Москвы и городов области из справочника площадок.
    """
    if not (city or "").strip():
        return False
    if is_moscow_city_name(city):
        return True
    return oblast_geo_area_for_city(city) is not None


def should_show_moscow_geo_fields(city: str) -> bool:
    """Показать селекты региона и района, пока город не выбран или он московский.

    Args:
        city: Название населённого пункта.

    Returns:
        bool: False только если указан город вне Москвы и области.
    """
    return not (city or "").strip() or city_uses_moscow_geo(city)


def geo_areas_client_payload() -> list[dict[str, str | int | list[str]]]:
    """Площадки Москвы и области для каскада и скрытия полей в формах.

    Returns:
        list[dict[str, str | int | list[str]]]: id, регион, название и псевдонимы.
    """
    from apps.core.models import GeoArea

    return [
        {
            "id": area.pk,
            "region": area.region,
            "name": area.name,
            "aliases": area.get_alias_list(),
        }
        for area in GeoArea.objects.filter(is_active=True).order_by(
            "region", "sort_order", "name"
        )
    ]


def area_label(region: str) -> str:
    """Вернуть название единицы деления региона.

    Args:
        region: Значение поля ``region``.

    Returns:
        str: «район» для Москвы, «город» для области, «площадка» по умолчанию.
    """
    return AREA_LABELS.get(region or "", "площадка")


def canonical_area_slug(slug: str) -> str:
    """Вернуть актуальный слаг района с учётом старых рекламных адресов.

    Args:
        slug: Слаг из URL или query-параметра.

    Returns:
        str: Текущий слаг либо исходная строка, если замены нет.
    """
    normalized = (slug or "").strip().lower()
    return LEGACY_AREA_SLUGS.get(normalized, normalized)
