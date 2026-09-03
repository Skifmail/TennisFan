"""Типы населённых пунктов РФ и разбор справочников KLADR/Yandex.

Справочник ``City`` исторически хранил только города. В РФ в том же поле
указывают пгт, сёла, деревни, станицы, хутора и аулы — этот модуль даёт
единый разбор типов, подписи для автодополнения и запись в справочник.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from django.db.models import TextChoices

logger = logging.getLogger(__name__)

SUGGEST_YANDEX_TIMEOUT = 2
YANDEX_SUGGEST_MIN_QUERY = 3
YANDEX_SUGGEST_LIMIT = 10


class SettlementType(TextChoices):
    """Тип населённого пункта в справочнике автодополнения."""

    CITY = "city", "город"
    PGT = "pgt", "пгт"
    POSELOK = "poselok", "посёлок"
    SELO = "selo", "село"
    DEREVNYA = "derevnya", "деревня"
    STANITSA = "stanitsa", "станица"
    KHUTOR = "khutor", "хутор"
    AUL = "aul", "аул"
    OTHER = "other", "населённый пункт"


def _choice_value(member: object) -> str:
    """Достать value из TextChoices.

    Без django-stubs mypy видит ``CITY = "city", "город"`` как кортеж,
    хотя в рантайме член перечисления — строка.
    """

    if isinstance(member, tuple):
        return str(member[0])
    value = getattr(member, "value", member)
    return value if isinstance(value, str) else str(value)


# Строковые значения для аннотаций и словарей (mypy без django-stubs).
ST_CITY = _choice_value(SettlementType.CITY)
ST_PGT = _choice_value(SettlementType.PGT)
ST_POSELOK = _choice_value(SettlementType.POSELOK)
ST_SELO = _choice_value(SettlementType.SELO)
ST_DEREVNYA = _choice_value(SettlementType.DEREVNYA)
ST_STANITSA = _choice_value(SettlementType.STANITSA)
ST_KHUTOR = _choice_value(SettlementType.KHUTOR)
ST_AUL = _choice_value(SettlementType.AUL)
ST_OTHER = _choice_value(SettlementType.OTHER)


KLADR_TYPE_MAP: dict[str, str] = {
    "г": ST_CITY,
    "город": ST_CITY,
    "пгт": ST_PGT,
    "гп": ST_PGT,
    "рп": ST_PGT,
    "п": ST_POSELOK,
    "пос": ST_POSELOK,
    "пг": ST_POSELOK,
    "кп": ST_POSELOK,
    "дп": ST_POSELOK,
    "с": ST_SELO,
    "село": ST_SELO,
    "д": ST_DEREVNYA,
    "дер": ST_DEREVNYA,
    "деревня": ST_DEREVNYA,
    "ст-ца": ST_STANITSA,
    "стца": ST_STANITSA,
    "станица": ST_STANITSA,
    "х": ST_KHUTOR,
    "хутор": ST_KHUTOR,
    "аул": ST_AUL,
    "нп": ST_OTHER,
    "сл": ST_OTHER,
    "у": ST_OTHER,
    "улус": ST_OTHER,
}

# Длинные префиксы первыми, чтобы «посёлок городского типа» не схлопнулся в «посёлок».
_TYPED_NAME_PREFIXES: tuple[tuple[str, str], ...] = (
    ("посёлок городского типа", ST_PGT),
    ("поселок городского типа", ST_PGT),
    ("рабочий посёлок", ST_PGT),
    ("рабочий поселок", ST_PGT),
    ("городской посёлок", ST_PGT),
    ("городской поселок", ST_PGT),
    ("курортный посёлок", ST_POSELOK),
    ("курортный поселок", ST_POSELOK),
    ("дачный посёлок", ST_POSELOK),
    ("дачный поселок", ST_POSELOK),
    ("посёлок", ST_POSELOK),
    ("поселок", ST_POSELOK),
    ("деревня", ST_DEREVNYA),
    ("станица", ST_STANITSA),
    ("хутор", ST_KHUTOR),
    ("село", ST_SELO),
    ("аул", ST_AUL),
    ("город", ST_CITY),
    ("пгт.", ST_PGT),
    ("пгт", ST_PGT),
    ("дер.", ST_DEREVNYA),
    ("ст-ца", ST_STANITSA),
)

_PREFIX_STRIP_RE = re.compile(
    r"^(?:г\.?|город|пгт\.?|пос[её]лок городского типа|рабочий пос[её]лок|"
    r"городской пос[её]лок|пос[её]лок|п\.|село|с\.|деревня|дер\.|д\.|"
    r"станица|ст-ца|хутор|х\.|аул)\s+",
    re.IGNORECASE,
)


def map_kladr_type(abbr: str, *, default: str = ST_OTHER) -> str:
    """Вернуть значение ``SettlementType`` по аббревиатуре КЛАДР.

    Args:
        abbr: Код типа из CSV (``г``, ``пгт``, ``д`` и т.д.).
        default: Тип, если аббревиатура пустая.

    Returns:
        Одно из значений ``SettlementType``.
    """

    key = (abbr or "").strip().lower().replace("ё", "е")
    if not key:
        return default
    return KLADR_TYPE_MAP.get(key, ST_OTHER)


def format_kladr_region(region_type: str, region: str) -> str:
    """Собрать читаемое название субъекта РФ.

    Args:
        region_type: Тип из КЛАДР (``обл``, ``Респ``, ``край``, ``г``).
        region: Короткое имя (``Архангельская``, ``Адыгея``).

    Returns:
        Строка вида «Архангельская область» или исходное имя, если тип неизвестен.
    """

    name = (region or "").strip()
    rt = (region_type or "").strip().lower().replace("ё", "е")
    if not name:
        return ""
    lowered = name.lower()
    if rt in {"г", "город"}:
        return name
    if rt in {"обл", "область"}:
        return name if "область" in lowered else f"{name} область"
    if rt in {"респ", "республика"}:
        if lowered.startswith("респ") or "республика" in lowered:
            return name
        return f"Республика {name}"
    if rt == "край":
        return name if "край" in lowered else f"{name} край"
    if rt in {"ао", "автономный округ"}:
        return name if "округ" in lowered else f"{name} АО"
    if rt in {"аобл", "автономная область"}:
        return name if "область" in lowered else f"{name} автономная область"
    return name


def _parse_optional_float(raw: str | None) -> float | None:
    """Разобрать координату из CSV; при ошибке вернуть None."""

    text = (raw or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_kladr_row(row: dict[str, str]) -> dict[str, Any] | None:
    """Разобрать строку KLADR-CSV в населённый пункт.

    Если заполнен ``settlement``, это и есть пункт (деревня, пгт и т.д.),
    а ``city`` — родительский город. Иначе берём ``city``.

    Args:
        row: Словарь колонок CSV.

    Returns:
        Словарь с ``name``, ``settlement_type``, ``region``, ``lat``, ``lng``
        либо ``None``, если нет ни города, ни поселения.
    """

    settlement = (row.get("settlement") or "").strip()
    city = (row.get("city") or "").strip()
    if settlement:
        name = settlement
        type_abbr = (row.get("settlement_type") or "").strip()
        settlement_type = map_kladr_type(type_abbr, default=ST_OTHER)
    elif city:
        name = city
        type_abbr = (row.get("city_type") or "").strip()
        settlement_type = map_kladr_type(type_abbr, default=ST_CITY)
    else:
        return None

    region = format_kladr_region(row.get("region_type") or "", row.get("region") or "")
    return {
        "name": name,
        "settlement_type": settlement_type,
        "region": region,
        "lat": _parse_optional_float(row.get("geo_lat")),
        "lng": _parse_optional_float(row.get("geo_lon")),
    }


def format_locality_label(name: str, settlement_type: str, region: str = "") -> str:
    """Подпись для автодополнения и значения поля формы.

    Города остаются как «Москва». Остальные типы — «деревня Кимжа, область».

    Args:
        name: Официальное имя без типа.
        settlement_type: Значение ``SettlementType``.
        region: Субъект РФ (для не-городов).

    Returns:
        Строка для показа пользователю.
    """

    clean_name = (name or "").strip()
    if not clean_name:
        return ""
    if settlement_type == ST_CITY:
        return clean_name
    try:
        type_label = SettlementType(settlement_type).label
    except ValueError:
        type_label = ""
    base = f"{type_label} {clean_name}".strip() if type_label else clean_name
    region_name = (region or "").strip()
    if region_name:
        return f"{base}, {region_name}"
    return base


def split_typed_name(raw: str) -> tuple[str, str]:
    """Отделить тип от имени («деревня Кимжа» → деревня, Кимжа).

    Args:
        raw: Строка из геокодера или ввода пользователя.

    Returns:
        Кортеж ``(settlement_type, name)``. Без префикса тип считается городом.
    """

    text = (raw or "").strip()
    if not text:
        return ST_CITY, ""
    lowered = text.casefold()
    for prefix, stype in _TYPED_NAME_PREFIXES:
        prefix_cf = prefix.casefold()
        if lowered.startswith(prefix_cf):
            rest = text[len(prefix) :].strip(" \t.,")
            if rest:
                return stype, rest
    return ST_CITY, text


def strip_settlement_prefix(raw: str) -> str:
    """Нормализовать имя для сравнения: без типа, нижний регистр, ё→е."""

    normalized = (raw or "").strip().lower().replace("ё", "е")
    normalized = _PREFIX_STRIP_RE.sub("", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def parse_yandex_locality(geo: dict[str, Any]) -> dict[str, Any] | None:
    """Разобрать GeoObject Яндекса, если это населённый пункт.

    Args:
        geo: Элемент ``GeoObject`` из ответа Geocoder API.

    Returns:
        Словарь как у ``parse_kladr_row`` плюс ``label``, либо ``None``.
    """

    meta = geo.get("metaDataProperty", {}).get("GeocoderMetaData", {}) or {}
    if meta.get("kind") != "locality":
        return None
    raw_name = (geo.get("name") or "").strip()
    if not raw_name:
        return None
    settlement_type, name = split_typed_name(raw_name)
    region = _extract_yandex_region(geo, meta)
    lat, lng = _yandex_point_to_lat_lng(geo)
    return {
        "name": name,
        "settlement_type": settlement_type,
        "region": region,
        "lat": lat,
        "lng": lng,
        "label": format_locality_label(name, settlement_type, region),
    }


def _extract_yandex_region(geo: dict[str, Any], meta: dict[str, Any]) -> str:
    """Достать субъект РФ из AddressDetails или description."""

    try:
        area = (
            meta.get("AddressDetails", {})
            .get("Country", {})
            .get("AdministrativeArea", {})
            .get("AdministrativeAreaName")
        )
        if area:
            return str(area).strip()
    except (AttributeError, TypeError):
        pass
    description = (geo.get("description") or "").strip()
    if not description:
        return ""
    parts = [part.strip() for part in description.split(",") if part.strip()]
    for part in parts:
        lowered = part.lower()
        if any(
            marker in lowered
            for marker in (
                "область",
                "край",
                "республика",
                "округ",
                "москва",
                "петербург",
            )
        ):
            return part
    if parts:
        return (
            parts[-1]
            if parts[-1].casefold() not in {"россия", "russia"}
            else (parts[-2] if len(parts) > 1 else "")
        )
    return ""


def _yandex_point_to_lat_lng(geo: dict[str, Any]) -> tuple[float | None, float | None]:
    """Извлечь (широта, долгота) из ``Point.pos`` Яндекса."""

    try:
        pos = geo["Point"]["pos"]
        lon_s, lat_s = str(pos).split()
        return float(lat_s), float(lon_s)
    except (KeyError, TypeError, ValueError):
        return None, None


def upsert_locality(
    *,
    name: str,
    settlement_type: str,
    region: str = "",
    lat: float | None = None,
    lng: float | None = None,
    force_update: bool = False,
) -> tuple[Any, bool]:
    """Создать или обновить запись справочника.

    Старые строки без региона обновляются на месте, чтобы не плодить дубли
    «Казань» / «Казань, Республика Татарстан».

    Args:
        name: Имя населённого пункта.
        settlement_type: Значение ``SettlementType``.
        region: Субъект РФ.
        lat: Широта.
        lng: Долгота.
        force_update: Обновлять координаты, даже если они уже есть.

    Returns:
        Кортеж ``(City, created)``.
    """

    from apps.core.models import City

    clean_name = (name or "").strip()
    clean_region = (region or "").strip()
    qs = City.objects.filter(name=clean_name, settlement_type=settlement_type)
    obj = qs.filter(region=clean_region).first()
    if obj is None and clean_region:
        obj = qs.filter(region="").first()
    if obj is None:
        same_name = list(qs[:2])
        if len(same_name) == 1:
            obj = same_name[0]

    if obj is None:
        obj = City.objects.create(
            name=clean_name,
            settlement_type=settlement_type,
            region=clean_region,
            lat=lat,
            lng=lng,
        )
        return obj, True

    changed: list[str] = []
    if clean_region and not obj.region:
        obj.region = clean_region
        changed.append("region")
    if (
        lat is not None
        and lng is not None
        and (force_update or obj.lat is None or obj.lng is None)
    ):
        obj.lat = lat
        obj.lng = lng
        changed.extend(["lat", "lng"])
    if changed:
        obj.save(update_fields=changed)
    return obj, False


def suggestion_from_parsed(parsed: dict[str, Any]) -> dict[str, str]:
    """Собрать JSON-элемент автодополнения из разобранного пункта."""

    label = parsed.get("label") or format_locality_label(
        str(parsed.get("name") or ""),
        str(parsed.get("settlement_type") or ST_CITY),
        str(parsed.get("region") or ""),
    )
    return {
        "name": str(parsed.get("name") or ""),
        "value": label,
        "label": label,
        "settlement_type": str(parsed.get("settlement_type") or ""),
        "region": str(parsed.get("region") or ""),
    }


def fetch_yandex_localities(
    query: str,
    *,
    api_key: str,
    referer: str = "",
    limit: int = YANDEX_SUGGEST_LIMIT,
) -> list[dict[str, Any]]:
    """Найти населённые пункты через Yandex Geocoder (kind=locality).

    Args:
        query: Строка из поля автодополнения.
        api_key: Ключ Geocoder API.
        referer: Referer, если ключ ограничен по домену.
        limit: Максимум результатов.

    Returns:
        Список словарей ``parse_yandex_locality``; пустой список при ошибке.
    """

    if not api_key or not (query or "").strip():
        return []

    from apps.courts.geocoder import _request_yandex

    geocode_query = query.strip()
    if "Россия" not in geocode_query and "Russia" not in geocode_query:
        geocode_query = f"{geocode_query}, Россия"

    members = _request_yandex(
        geocode_query,
        api_key=api_key,
        lang="ru_RU",
        referer=referer or None,
        results=limit,
        kind="locality",
    )
    if not members:
        return []

    seen: set[tuple[str, str, str]] = set()
    results: list[dict[str, Any]] = []
    for item in members:
        geo = item.get("GeoObject") if isinstance(item, dict) else None
        if not isinstance(geo, dict):
            continue
        parsed = parse_yandex_locality(geo)
        if parsed is None:
            continue
        key = (
            str(parsed["name"]).casefold(),
            str(parsed["settlement_type"]),
            str(parsed["region"]).casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        results.append(parsed)
    return results
