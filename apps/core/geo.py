"""География платформы: регионы проведения турниров и тренировок.

Регион — верхний уровень навигации: Москва делится на районы, область — на
города. Перечисление, а не таблица в базе: значений два и они не меняются,
а редактируемый уровень вынесен в модель ``apps.core.models.GeoArea``.
"""

from django.db import models


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
