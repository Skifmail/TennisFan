"""Посадочные страницы турниров: регион, зона/город и формат в адресе.

Реклама ведёт на конкретное направление, поэтому фильтры вынесены в путь, а не
в query-параметры: адрес остаётся читаемым, страница индексируется и её можно
подставить в объявление без потери контекста.
"""

from dataclasses import dataclass

from django.http import Http404
from django.urls import reverse

from apps.core.geo import GeoRegion, region_from_slug, region_to_slug
from apps.core.models import GeoArea

from .models import TournamentVariant

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


@dataclass(frozen=True)
class TournamentLanding:
    """Разобранные параметры посадочной страницы турниров.

    Attributes:
        region: Значение поля ``region`` или пустая строка для общего каталога.
        area: Зона Москвы или город области, если страница сужена до неё.
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
            str: Путь вида ``/tournaments/moscow/yugo-vostok/singles/``.
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
            str: Например «Парные турниры по теннису в Москве, Юго-Восток».
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
                "Любительские турниры по теннису в Москве и Московской области: "
                "одиночные и парные, набор по уровню. Сначала собираем группу — "
                "место подбираем с учётом участников."
            )
        parts = [self.heading + "."]
        parts.append(
            "Запись онлайн: старт после набора группы, корт и время матчей "
            "согласуете с соперником."
        )
        return " ".join(parts)


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


def geo_area_choices(region: str = "") -> list[GeoArea]:
    """Вернуть активные зоны и города для выпадающего списка фильтра.

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


def resolve_landing(
    region_slug: str | None = None,
    area_slug: str | None = None,
    variant_slug: str | None = None,
) -> TournamentLanding:
    """Разобрать параметры посадочной страницы.

    Args:
        region_slug: Слаг региона из адреса или query-параметра.
        area_slug: Слаг зоны Москвы либо города области.
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
        area = GeoArea.objects.filter(slug=area_slug, is_active=True).first()
        if area is None:
            raise Http404("Неизвестная зона или город")
        return TournamentLanding(region=area.region, area=area, variant=variant)

    region = region_from_slug(region_slug)
    if region is None:
        raise Http404("Неизвестный регион")

    area = None
    if area_slug:
        area = GeoArea.objects.filter(
            slug=area_slug,
            region=region,
            is_active=True,
        ).first()
        if area is None:
            raise Http404("Неизвестная зона или город")

    return TournamentLanding(region=region, area=area, variant=variant)


def iter_sitemap_landings() -> list[TournamentLanding]:
    """Страницы каталога для sitemap: регионы, зоны/города и форматы.

    Не плодим матрицу «каждая зона × каждый формат» как отдельные рекламные
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
