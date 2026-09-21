"""Посадочные страницы турниров: регион, район/город и формат в адресе.

Реклама ведёт на конкретное направление, поэтому фильтры вынесены в путь, а не
в query-параметры: адрес остаётся читаемым, страница индексируется и её можно
подставить в объявление без потери контекста.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from django.http import Http404
from django.urls import reverse

from apps.core.geo import (
    GeoRegion,
    canonical_area_slug,
    region_from_slug,
    region_to_slug,
)
from apps.core.models import GeoArea

from .models import TournamentVariant
from .platform_home import CLUB_FILTER_CLUB_ONLY, CLUB_FILTER_PLATFORM

#: Слаг формата в адресе → значение поля ``variant``.
VARIANT_SLUGS: dict[str, str] = {
    "singles": "singles",
    "doubles": "doubles",
}

#: Значение поля ``variant`` → слаг формата в адресе.
VARIANT_BY_VALUE: dict[str, str] = {
    value: slug for slug, value in VARIANT_SLUGS.items()
}

#: Прилагательное для заголовка страницы по формату.
VARIANT_WORDS: dict[str, str] = {
    "singles": "Одиночные",
    "doubles": "Парные",
}

#: Название региона в предложном падеже — для заголовков.
REGION_IN: dict[str, str] = {
    "moscow": "в Москве",
    "moscow_oblast": "в Московской области",
}

#: Подписи фильтра статуса на каталоге (не архив).
STATUS_FILTER_LABELS: dict[str, str] = {
    "upcoming": "Предстоящие",
    "active": "Активные",
    "completed": "Завершённые",
}

#: Пресеты query-параметра ``club`` на каталоге.
CLUB_FILTER_CHIP_LABELS: dict[str, str] = {
    CLUB_FILTER_PLATFORM: "TennisFan",
    CLUB_FILTER_CLUB_ONLY: "Только клубные",
}


@dataclass(frozen=True)
class TournamentLanding:
    """Разобранные параметры посадочной страницы турниров.

    Attributes:
        region: Значение поля ``region`` или пустая строка для общего каталога.
        area: Район Москвы или город области, если страница сужена до неё.
        variant: Значение поля ``variant`` или пустая строка для всех форматов.
    """

    region: str = ""
    area: GeoArea | None = None
    variant: str = ""

    @property
    def is_filtered(self) -> bool:
        """Задан ли хотя бы один параметр географии или формата.

        Returns:
            bool: True, если страница не является общим каталогом.
        """
        return bool(self.region or self.area or self.variant)

    @property
    def url(self) -> str:
        """Собрать канонический адрес страницы.

        Returns:
            str: Путь вида ``/tournaments/moscow/yug/singles/``.
        """
        base = str(reverse("tournament_list"))
        if not self.region:
            return base
        parts = [region_to_slug(self.region)]
        if self.area is not None:
            parts.append(self.area.slug)
        if self.variant:
            parts.append(VARIANT_BY_VALUE[self.variant])
        return f"{base}{'/'.join(parts)}/"

    @property
    def heading(self) -> str:
        """Собрать заголовок H1 страницы.

        Returns:
            str: Например «Парные турниры по теннису в Москве, Юг».
        """
        prefix = VARIANT_WORDS.get(self.variant, "Любительские")
        place = REGION_IN.get(self.region, "")
        if self.area is not None:
            place = f"{place}, {self.area.name}" if place else self.area.name
        return f"{prefix} турниры по теннису {place}".strip()

    @property
    def meta_description(self) -> str:
        """Краткое описание для meta description и Open Graph.

        Returns:
            str: Текст до ~160 символов для поисковой выдачи.
        """
        if not self.is_filtered:
            return (
                "Любительские турниры по теннису в Москве и области. "
                "Играйте с соперниками своего уровня. "
                "Точное место проведения сообщим, когда завершится набор."
            )
        return (
            f"{self.heading}. Играйте с соперниками своего уровня. "
            "Точное место проведения сообщим, когда завершится набор."
        )


def region_options() -> list[dict[str, str]]:
    """Вернуть регионы для выпадающего списка фильтра.

    Returns:
        list[dict[str, str]]: Слаг для адреса и подпись для интерфейса.
    """
    return [
        {"slug": region_to_slug(value), "label": label}
        for value, label in GeoRegion.choices
    ]


def variant_options() -> list[dict[str, str]]:
    """Вернуть форматы турниров для выпадающего списка фильтра.

    Returns:
        list[dict[str, str]]: Слаг для адреса и подпись для интерфейса.
    """
    return [
        {"slug": VARIANT_BY_VALUE[value], "label": label}
        for value, label in TournamentVariant.choices
    ]


def _option_label(slug: str, options: list[dict[str, str]]) -> str:
    """Найти подпись опции по слагу.

    Args:
        slug: Выбранное значение фильтра.
        options: Список словарей со ``slug`` и ``label``.

    Returns:
        str: Подпись опции либо сам слаг, если совпадения нет.
    """
    for option in options:
        if option["slug"] == slug:
            return option["label"]
    return slug


def build_tournament_filter_chips(
    *,
    current_region: str,
    region_opts: list[dict[str, str]],
    current_area_name: str,
    current_variant: str,
    variant_opts: list[dict[str, str]],
    current_city: str,
    current_category: str,
    category_choices: Iterable[tuple[str, str]],
    current_status: str,
    is_archive: bool,
    club_filter: str,
    club_choices: Iterable[tuple[str, str]],
) -> list[str]:
    """Собрать короткие подписи активных фильтров для мобильной панели.

    Пустые значения (все регионы, все форматы и т.д.) в чипы не попадают,
    чтобы свёрнутая панель оставалась короткой.

    Args:
        current_region: Слаг выбранного региона.
        region_opts: Опции фильтра региона.
        current_area_name: Название выбранного района или города.
        current_variant: Слаг выбранного формата.
        variant_opts: Опции фильтра формата.
        current_city: Строка населённого пункта.
        current_category: Код уровня игроков.
        category_choices: Пары ``(value, label)`` уровней.
        current_status: Код статуса турнира.
        is_archive: Архив всегда завершённые, чип статуса там не нужен.
        club_filter: Слаг клуба или пресет ``__platform__`` / ``__club_only__``.
        club_choices: Пары ``(slug, name)`` клубов.

    Returns:
        list[str]: Подписи в порядке полей формы.
    """
    chips: list[str] = []
    if current_region:
        chips.append(_option_label(current_region, region_opts))
    if current_area_name:
        chips.append(current_area_name)
    if current_variant:
        chips.append(_option_label(current_variant, variant_opts))
    if current_city:
        chips.append(current_city)
    if current_category:
        chips.append(dict(category_choices).get(current_category, current_category))
    if current_status and not is_archive:
        chips.append(STATUS_FILTER_LABELS.get(current_status, current_status))
    if club_filter:
        chips.append(
            CLUB_FILTER_CHIP_LABELS.get(
                club_filter,
                dict(club_choices).get(club_filter, club_filter),
            )
        )
    return chips


def geo_area_choices(region: str = "") -> list[GeoArea]:
    """Вернуть активные районы и города для выпадающего списка фильтра.

    Args:
        region: Ограничить одним регионом; пустая строка — все регионы.

    Returns:
        list[GeoArea]: Площадки в порядке сортировки справочника.
    """
    queryset = GeoArea.objects.filter(is_active=True)
    if region:
        queryset = queryset.filter(region=region)
    areas = list(queryset)
    # Шаблону нужен слаг региона, чтобы собрать ссылку на площадку без запроса
    # к справочнику регионов на каждой итерации.
    for area in areas:
        area.region_slug = region_to_slug(area.region)
    return areas


def _resolve_variant(variant_slug: str | None) -> str:
    """Проверить слаг формата и вернуть значение поля.

    Args:
        variant_slug: Слаг из адреса или query-параметра.

    Returns:
        str: Значение поля ``variant`` или пустая строка.

    Raises:
        Http404: Если слаг задан, но неизвестен.
    """
    if not variant_slug:
        return ""
    value = VARIANT_SLUGS.get(variant_slug.strip().lower())
    if value is None:
        raise Http404("Неизвестный формат турнира")
    return value


def _active_area_by_slug(area_slug: str, region: str | None = None) -> GeoArea | None:
    """Найти активную площадку по текущему или устаревшему слагу.

    Ищем оба варианта, чтобы рекламные адреса не отдавали 404, если код
    и миграция справочника выкатываются не одновременно.

    Args:
        area_slug: Слаг из адреса или query-параметра, уже в нижнем регистре.
        region: Ограничить поиск одним регионом.

    Returns:
        GeoArea | None: Площадка с каноническим слагом, если она есть,
        иначе запись со старым слагом.
    """
    if not area_slug:
        return None
    slugs = {area_slug, canonical_area_slug(area_slug)}
    queryset = GeoArea.objects.filter(slug__in=slugs, is_active=True)
    if region:
        queryset = queryset.filter(region=region)
    areas = list(queryset)
    if not areas:
        return None
    wanted = canonical_area_slug(area_slug)
    for area in areas:
        if area.slug == wanted:
            return cast(GeoArea, area)
    return cast(GeoArea, areas[0])


def resolve_landing(
    region_slug: str | None = None,
    area_slug: str | None = None,
    variant_slug: str | None = None,
) -> TournamentLanding:
    """Разобрать параметры посадочной страницы.

    Args:
        region_slug: Слаг региона из адреса или query-параметра.
        area_slug: Слаг района Москвы либо города области.
        variant_slug: ``singles`` или ``doubles``.

    Returns:
        TournamentLanding: Разобранные параметры страницы.

    Raises:
        Http404: Если регион, площадка или формат неизвестны либо площадка
            относится к другому региону.
    """
    variant = _resolve_variant(variant_slug)
    area: GeoArea | None = None

    area_slug = (area_slug or "").strip().lower()

    if not region_slug:
        if not area_slug:
            return TournamentLanding(variant=variant)
        # Площадку можно выбрать в фильтре, не указав регион: слаги уникальны,
        # поэтому регион выводим из самой площадки.
        area = _active_area_by_slug(area_slug)
        if area is None:
            raise Http404("Неизвестный район или город")
        return TournamentLanding(region=area.region, area=area, variant=variant)

    region = region_from_slug(region_slug)
    if region is None:
        raise Http404("Неизвестный регион")

    area = None
    if area_slug:
        area = _active_area_by_slug(area_slug, region=region)
        if area is None:
            raise Http404("Неизвестный район или город")

    return TournamentLanding(region=region, area=area, variant=variant)


def iter_sitemap_landings() -> list[TournamentLanding]:
    """Страницы каталога для sitemap: регионы, районы/города и форматы.

    Не плодим матрицу «каждый район × каждый формат» как отдельные рекламные
    направления: в индекс идут общий каталог (через static sitemap), регионы,
    регион+формат и регион+площадка. Новая площадка из справочника попадает
    сюда без правок кода.

    Returns:
        list[TournamentLanding]: Уникальные посадочные URL.
    """
    landings: list[TournamentLanding] = []
    areas = list(
        GeoArea.objects.filter(is_active=True).order_by("region", "sort_order")
    )
    for region, _label in GeoRegion.choices:
        landings.append(TournamentLanding(region=region))
        for variant in ("singles", "doubles"):
            landings.append(TournamentLanding(region=region, variant=variant))
        for area in areas:
            if area.region != region:
                continue
            landings.append(TournamentLanding(region=region, area=area))
    return landings
