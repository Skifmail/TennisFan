"""
Геокодирование адреса в координаты для кортов через Yandex Geocoder API.
Сначала ищем в области города (ll, spn, rspn=1). Если там только сам город,
повторяем с мягким смещением к центру города (rspn=0) и берём дом/улицу,
только если точка не уехала слишком далеко от hint_city.
"""

import logging
import math
from typing import Any, cast

import requests

from apps.core.geo import normalize_geo_text

logger = logging.getLogger(__name__)

TIMEOUT = 10
REQUEST_HEADERS = {
    "User-Agent": "TennisFan/1.0 (courts geocoding; contact: site admin)"
}

YANDEX_GEOCODER_URL = "https://geocode-maps.yandex.ru/v1/"

# Жёсткая рамка ~15 км; мягкий повтор допускает ближнее Подмосковье, но не другой регион.
CITY_SPAN_LON, CITY_SPAN_LAT = 0.15, 0.15
FALLBACK_MAX_DEG = 0.45

KIND_RANK = {
    "house": 1,
    "street": 2,
    "metro": 3,
    "district": 4,
    "locality": 5,
    "area": 6,
    "province": 7,
    "country": 8,
    "other": 9,
}
PRECISION_RANK = {
    "exact": 1,
    "number": 2,
    "near": 3,
    "range": 4,
    "street": 5,
    "other": 6,
}

# Дом/улица — точное попадание; метро/район/город часто дают центр Москвы.
PRECISE_GEO_KINDS = frozenset({"house", "street"})


def _request_yandex(
    geocode_query: str,
    *,
    api_key: str,
    lang: str,
    referer: str | None,
    ll: tuple[float, float] | None = None,
    spn: tuple[float, float] | None = None,
    rspn: int = 0,
    results: int = 10,
    kind: str | None = None,
) -> list | None:
    """Один запрос к Yandex Geocoder. Возвращает featureMember или None."""
    params: dict[str, str | int] = {
        "apikey": api_key,
        "geocode": geocode_query,
        "lang": lang,
        "format": "json",
        "results": results,
    }
    if kind:
        params["kind"] = kind
    if ll is not None and spn is not None:
        params["ll"] = f"{ll[0]},{ll[1]}"
        params["spn"] = f"{spn[0]},{spn[1]}"
        params["rspn"] = rspn
    headers = dict(REQUEST_HEADERS)
    if referer:
        headers["Referer"] = referer.rstrip("/")
    try:
        resp = requests.get(
            YANDEX_GEOCODER_URL,
            params=params,
            timeout=TIMEOUT,
            headers=headers,
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                logger.warning(
                    "Yandex Geocoder error %s: %s",
                    resp.status_code,
                    err_body.get("message", err_body),
                )
            except Exception:
                logger.warning(
                    "Yandex Geocoder error %s: %s",
                    resp.status_code,
                    resp.text[:200] if resp.text else "",
                )
            return None
        data = resp.json()
        members = (
            data.get("response", {})
            .get("GeoObjectCollection", {})
            .get("featureMember", [])
        )
        return cast(list[Any], members) if members is not None else None
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning("Yandex Geocoder request failed: %s", e)
        return None


def _pick_best_member(members: list) -> dict | None:
    """Выбрать из списка GeoObject наиболее точный (дом > улица > район > город)."""
    if not members:
        return None
    best = None
    best_rank = (999, 999)
    for item in members:
        geo = item.get("GeoObject", {})
        meta = geo.get("metaDataProperty", {}).get("GeocoderMetaData", {})
        kind_r = KIND_RANK.get(meta.get("kind", "other"), 99)
        prec_r = PRECISION_RANK.get(meta.get("precision", "other"), 99)
        if (kind_r, prec_r) < best_rank:
            best_rank = (kind_r, prec_r)
            best = geo
    result = best or members[0].get("GeoObject")
    return cast(dict | None, result)


def _pos_to_lat_lon(geo: dict) -> tuple[float | None, float | None]:
    """Из GeoObject извлечь (lat, lon)."""
    try:
        pos = geo["Point"]["pos"]  # "longitude latitude"
        parts = pos.split()
        if len(parts) != 2:
            return None, None
        lon, lat = float(parts[0]), float(parts[1])
        return lat, lon
    except (KeyError, TypeError, ValueError):
        return None, None


def _geo_kind(geo: dict | None) -> str:
    """Вернуть kind GeoObject (house, locality, province, ...)."""
    if not geo:
        return "other"
    meta_prop = geo.get("metaDataProperty") or {}
    if not isinstance(meta_prop, dict):
        return "other"
    meta = meta_prop.get("GeocoderMetaData") or {}
    if not isinstance(meta, dict):
        return "other"
    return str(meta.get("kind") or "other")


def _is_precise_geo(geo: dict | None) -> bool:
    """True, если точка привязана к дому или улице, а не к центру города."""
    return _geo_kind(geo) in PRECISE_GEO_KINDS


def _deg_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Грубая дистанция в градусах, чтобы отсечь другой регион."""
    return math.hypot(lat1 - lat2, lon1 - lon2)


def _is_near_hint(geo: dict | None, hint_ll: tuple[float, float] | None) -> bool:
    """True, если точка рядом с центром hint_city или подсказки нет."""
    if hint_ll is None or not geo:
        return True
    lat, lon = _pos_to_lat_lon(geo)
    if lat is None or lon is None:
        return False
    hint_lon, hint_lat = hint_ll
    return _deg_distance(lat, lon, hint_lat, hint_lon) <= FALLBACK_MAX_DEG


def _geocode_yandex(
    address: str,
    *,
    api_key: str,
    lang: str = "ru_RU",
    referer: str | None = None,
    hint_city: str | None = None,
) -> tuple[float | None, float | None]:
    """
    Координаты через Yandex Geocoder API.
    Если задан hint_city — сначала ищем в области города (~15 км), чтобы не уехать
    в другой регион с той же улицей. Если в рамке находится только сам город
    (типичный центр Москвы), повторяем с rspn=0 и берём дом/улицу рядом с городом.
    """
    if not api_key or not (address or "").strip():
        return None, None

    geocode_query = address.strip()
    if "Россия" not in geocode_query and "Russia" not in geocode_query:
        geocode_query = f"{geocode_query}, Россия"

    ll, spn = None, None
    if hint_city and (hint_city := hint_city.strip()):
        city_query = f"{hint_city}, Россия"
        city_members = _request_yandex(
            city_query, api_key=api_key, lang=lang, referer=referer, results=1
        )
        if city_members:
            city_geo = city_members[0].get("GeoObject", {})
            lat, lon = _pos_to_lat_lon(city_geo)
            if lat is not None and lon is not None:
                ll = (lon, lat)
                spn = (CITY_SPAN_LON, CITY_SPAN_LAT)

    used_bbox = ll is not None and spn is not None
    members = _request_yandex(
        geocode_query,
        api_key=api_key,
        lang=lang,
        referer=referer,
        ll=ll,
        spn=spn,
        rspn=1 if used_bbox else 0,
    )
    best = _pick_best_member(members) if members else None
    if used_bbox and not _is_precise_geo(best):
        # Рамка ~15 км часто возвращает locality «Москва» вместо дома в области.
        fallback_members = _request_yandex(
            geocode_query,
            api_key=api_key,
            lang=lang,
            referer=referer,
            ll=ll,
            spn=spn,
            rspn=0,
        )
        fallback_best = (
            _pick_best_member(fallback_members) if fallback_members else None
        )
        if fallback_best and _is_near_hint(fallback_best, ll):
            fallback_rank = KIND_RANK.get(_geo_kind(fallback_best), 99)
            best_rank = KIND_RANK.get(_geo_kind(best), 99)
            if (
                _is_precise_geo(fallback_best)
                or best is None
                or fallback_rank < best_rank
            ):
                best = fallback_best
    if not best:
        return None, None
    return _pos_to_lat_lon(best)


def _normalize_address_for_geocode(city: str, address: str) -> str:
    """
    Собрать одну строку адреса без дублирования города.
    Рекомендуемый ввод: Город — только город; Адрес — улица, номер дома.
    """
    city = (city or "").strip()
    address = (address or "").strip()
    if not address:
        return city
    if not city:
        return address
    if address.lower().startswith(city.lower() + ",") or address.lower().startswith(
        city.lower() + " "
    ):
        return address
    return f"{city}, {address}"


_LOCALITY_PREFIXES = (
    "город",
    "посёлок",
    "поселок",
    "деревня",
    "пгт.",
    "пгт",
    "дер.",
    "пос.",
    "село",
    "г.",
    "г",
    "с.",
)


def _locality_key(text: str) -> str:
    """Нормализовать часть адреса для сравнения с населённым пунктом."""
    key = normalize_geo_text(text)
    for prefix in _LOCALITY_PREFIXES:
        prefix_key = normalize_geo_text(prefix)
        if key.startswith(f"{prefix_key} "):
            return key[len(prefix_key) :].strip()
    return key


def format_court_display_address(city: str, address: str) -> str:
    """Вернуть адрес корта без повторённого населённого пункта.

    Город уже выводится отдельной строкой, поэтому из адреса убираем
    совпадающие части и подряд идущие дубли.

    Args:
        city: Населённый пункт корта.
        address: Сырая строка адреса, часто из геокодера.

    Returns:
        str: Адрес для карточки. Пустая строка, если кроме города ничего нет.
    """
    city_key = _locality_key(city)
    parts: list[str] = []
    previous_key = ""
    for raw in (address or "").split(","):
        part = " ".join(raw.split())
        if not part:
            continue
        key = _locality_key(part)
        if city_key and key == city_key:
            continue
        if key and key == previous_key:
            continue
        parts.append(part)
        previous_key = key
    return ", ".join(parts)


def geocode_address(
    address: str,
    *,
    api_key: str = "",
    lang: str = "ru_RU",
    referer: str | None = None,
    hint_city: str | None = None,
) -> tuple[float | None, float | None]:
    """
    Преобразовать адрес в координаты (широта, долгота) через Yandex Geocoder API.
    hint_city сужает первый запрос областью города; если там находится только
    сам город, а не дом/улица, повторяем поиск без рамки.

    :param address: Строка адреса (город, улица, дом).
    :param api_key: API-ключ Яндекса (обязателен для работы).
    :param referer: Значение Referer, если у ключа ограничение по Referer.
    :param hint_city: Город для ограничения области поиска (рекомендуется заполнять поле «Город» в админке).
    :return: (latitude, longitude) или (None, None).
    """
    if not (address or "").strip():
        return None, None
    return _geocode_yandex(
        address,
        api_key=api_key,
        lang=lang,
        referer=referer,
        hint_city=hint_city,
    )
