"""Чистый расчёт географического расстояния между двумя точками."""

import math
from typing import Final

EARTH_RADIUS_KM: Final = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Рассчитывает расстояние по поверхности Земли.

    Args:
        lat1: Широта первой точки в градусах.
        lng1: Долгота первой точки в градусах.
        lat2: Широта второй точки в градусах.
        lng2: Долгота второй точки в градусах.

    Returns:
        Расстояние по большой окружности в километрах.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lng / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
