"""
Поиск заявок и игроков рядом с населённым пунктом пользователя.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from django.db.models import Q, QuerySet

from apps.core.models import City
from apps.users.models import Player

NEARBY_RADIUS_CHOICES: tuple[int, ...] = (10, 25, 50, 100)
NEARBY_DEFAULT_RADIUS: int = 25


@dataclass(frozen=True)
class NearbyPlace:
    """Населённый пункт с координатами и расстоянием до точки отсчёта."""

    name: str
    lat: float
    lng: float
    distance_km: float


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Расстояние между двумя точками по поверхности Земли, в километрах."""
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lng / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def coordinates_for_locality(name: str) -> tuple[float, float] | None:
    """Координаты населённого пункта из справочника.

    Args:
        name: Название, как оно записано у игрока или в заявке.

    Returns:
        Пара широта/долгота либо ``None``, если пункт без координат.
    """
    cleaned = name.strip()
    if not cleaned:
        return None
    city = (
        City.objects.filter(name__iexact=cleaned, lat__isnull=False, lng__isnull=False)
        .order_by("pk")
        .first()
    )
    if city is None or city.lat is None or city.lng is None:
        return None
    return float(city.lat), float(city.lng)


def places_within_radius(
    origin_lat: float,
    origin_lng: float,
    radius_km: int,
) -> list[NearbyPlace]:
    """Населённые пункты справочника в радиусе от точки.

    Одноимённые пункты схлопываются: остаётся ближайший к точке отсчёта.
    """
    closest: dict[str, NearbyPlace] = {}
    cities = City.objects.filter(lat__isnull=False, lng__isnull=False).only(
        "name", "lat", "lng"
    )
    for city in cities:
        if city.lat is None or city.lng is None:
            continue
        distance = haversine_km(
            origin_lat, origin_lng, float(city.lat), float(city.lng)
        )
        if distance > radius_km:
            continue
        key = city.name.casefold()
        current = closest.get(key)
        if current is None or distance < current.distance_km:
            closest[key] = NearbyPlace(
                name=city.name,
                lat=float(city.lat),
                lng=float(city.lng),
                distance_km=distance,
            )
    return list(closest.values())


def filter_by_locality_names(qs: QuerySet, names: list[str]) -> QuerySet:
    """Оставить объекты, чей город совпадает с одним из названий."""
    if not names:
        return qs.none()
    combined = Q()
    for name in names:
        combined |= Q(city__iexact=name)
    return qs.filter(combined)


def nearby_players(
    *,
    places: list[NearbyPlace],
    exclude_player_id: int | None,
    limit: int = 20,
) -> list[Player]:
    """Игроки из населённых пунктов рядом, ближайшие первыми.

    Служебные игроки bye не попадают в список.
    """
    if not places:
        return []
    distance_by_name = {place.name.casefold(): place.distance_km for place in places}
    players = Player.objects.filter(is_bye=False).select_related("user")
    if exclude_player_id is not None:
        players = players.exclude(pk=exclude_player_id)
    players = filter_by_locality_names(players, [place.name for place in places])
    ranked: list[tuple[float, Player]] = []
    for player in players[:500]:
        distance = distance_by_name.get(player.city.casefold())
        if distance is None:
            continue
        player.distance_km = round(distance, 1)
        ranked.append((distance, player))
    ranked.sort(key=lambda item: (item[0], item[1].pk))
    return [player for _, player in ranked[:limit]]
