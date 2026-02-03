"""
Yandex Geocoder API — получение координат по адресу.
Используется при сохранении корта в админке и для действия «Получить координаты по адресу».
"""

import logging
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

GEOCODER_URL = "https://geocode-maps.yandex.ru/v1/"
TIMEOUT = 10


def geocode_address(
    address: str,
    *,
    api_key: str,
    lang: str = "ru_RU",
) -> Tuple[Optional[float], Optional[float]]:
    """
    Преобразовать адрес в координаты (широта, долгота) через Yandex Geocoder API.

    :param address: Строка адреса (город, улица, дом и т.д.).
    :param api_key: API-ключ из кабинета разработчика Яндекса (Geocoder API).
    :param lang: Язык ответа (ru_RU по умолчанию).
    :return: (latitude, longitude) или (None, None) при ошибке или отсутствии результата.
    """
    if not api_key or not (address or "").strip():
        return None, None

    params = {
        "apikey": api_key,
        "geocode": address.strip(),
        "lang": lang,
        "format": "json",
    }

    try:
        resp = requests.get(GEOCODER_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning("Yandex Geocoder request failed: %s", e)
        return None, None
    except (ValueError, KeyError) as e:
        logger.warning("Yandex Geocoder response parse error: %s", e)
        return None, None

    try:
        collection = data["response"]["GeoObjectCollection"]
        members = collection.get("featureMember", [])
        if not members:
            return None, None
        geo = members[0]["GeoObject"]
        pos = geo["Point"]["pos"]  # "longitude latitude" (порядок в API: долгота широта)
        parts = pos.split()
        if len(parts) != 2:
            return None, None
        lon = float(parts[0])
        lat = float(parts[1])
        return lat, lon
    except (KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning("Yandex Geocoder unexpected response structure: %s", e)
        return None, None
