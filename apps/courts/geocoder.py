"""
Геокодирование адреса в координаты для кортов через Yandex Geocoder API.
При указании города поиск ограничивается областью города (ll, spn, rspn=1), чтобы точка не уезжала за сотни км.
"""

import logging
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 10
REQUEST_HEADERS = {"User-Agent": "TennisFan/1.0 (courts geocoding; contact: site admin)"}

YANDEX_GEOCODER_URL = "https://geocode-maps.yandex.ru/v1/"

# Размер области вокруг города (градусы: ~0.15 ≈ 15 км), чтобы ограничить поиск адреса
CITY_SPAN_LON, CITY_SPAN_LAT = 0.15, 0.15

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
PRECISION_RANK = {"exact": 1, "number": 2, "near": 3, "range": 4, "street": 5, "other": 6}


def _request_yandex(
    geocode_query: str,
    *,
    api_key: str,
    lang: str,
    referer: Optional[str],
    ll: Optional[Tuple[float, float]] = None,
    spn: Optional[Tuple[float, float]] = None,
    rspn: int = 0,
    results: int = 10,
) -> Optional[list]:
    """Один запрос к Yandex Geocoder. Возвращает featureMember или None."""
    params = {
        "apikey": api_key,
        "geocode": geocode_query,
        "lang": lang,
        "format": "json",
        "results": results,
    }
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
                logger.warning("Yandex Geocoder error %s: %s", resp.status_code, err_body.get("message", err_body))
            except Exception:
                logger.warning("Yandex Geocoder error %s: %s", resp.status_code, resp.text[:200] if resp.text else "")
            return None
        data = resp.json()
        return data.get("response", {}).get("GeoObjectCollection", {}).get("featureMember", [])
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.warning("Yandex Geocoder request failed: %s", e)
        return None


def _pick_best_member(members: list) -> Optional[dict]:
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
    return best or members[0].get("GeoObject")


def _pos_to_lat_lon(geo: dict) -> Tuple[Optional[float], Optional[float]]:
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


def _geocode_yandex(
    address: str,
    *,
    api_key: str,
    lang: str = "ru_RU",
    referer: Optional[str] = None,
    hint_city: Optional[str] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Координаты через Yandex Geocoder API.
    Если задан hint_city — сначала получаем центр города, затем ищем адрес только в этой области (rspn=1),
    чтобы не получить точку в другом регионе с тем же названием улицы.
    """
    if not api_key or not (address or "").strip():
        return None, None

    geocode_query = address.strip()
    if "Россия" not in geocode_query and "Russia" not in geocode_query:
        geocode_query = f"{geocode_query}, Россия"

    ll, spn = None, None
    if hint_city and (hint_city := hint_city.strip()):
        city_query = f"{hint_city}, Россия"
        city_members = _request_yandex(city_query, api_key=api_key, lang=lang, referer=referer, results=1)
        if city_members:
            city_geo = city_members[0].get("GeoObject", {})
            lat, lon = _pos_to_lat_lon(city_geo)
            if lat is not None and lon is not None:
                ll = (lon, lat)
                spn = (CITY_SPAN_LON, CITY_SPAN_LAT)

    members = _request_yandex(
        geocode_query,
        api_key=api_key,
        lang=lang,
        referer=referer,
        ll=ll,
        spn=spn,
        rspn=1 if (ll and spn) else 0,
    )
    if not members and ll and spn:
        # В области города ничего не нашли — пробуем без ограничения (на случай ошибки границ)
        members = _request_yandex(geocode_query, api_key=api_key, lang=lang, referer=referer)
    if not members:
        return None, None
    best = _pick_best_member(members)
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
    if address.lower().startswith(city.lower() + ",") or address.lower().startswith(city.lower() + " "):
        return address
    return f"{city}, {address}"


def geocode_address(
    address: str,
    *,
    api_key: str = "",
    lang: str = "ru_RU",
    referer: Optional[str] = None,
    hint_city: Optional[str] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Преобразовать адрес в координаты (широта, долгота) через Yandex Geocoder API.
    hint_city: если задан, поиск ограничивается областью этого города (~15 км), чтобы не получить точку в другом регионе.

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
