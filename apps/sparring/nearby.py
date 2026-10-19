"""
Поиск заявок и игроков рядом с населённым пунктом пользователя.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from django.db.models import Q, QuerySet

from apps.core.localities import locality_tokens, strip_settlement_prefix
from apps.core.models import City
from apps.users.models import Player

NEARBY_RADIUS_CHOICES: tuple[int, ...] = (10, 25, 50, 100)
NEARBY_PLAYERS_PAGE_SIZE: int = 20


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
    candidates = [name.strip(), *locality_tokens(name)]
    seen: set[str] = set()
    for candidate in candidates:
        folded = candidate.casefold()
        if not candidate or folded in seen:
            continue
        seen.add(folded)
        city = (
            City.objects.filter(
                name__iexact=candidate, lat__isnull=False, lng__isnull=False
            )
            .order_by("pk")
            .first()
        )
        if city is not None and city.lat is not None and city.lng is not None:
            return float(city.lat), float(city.lng)
    return None


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


def narrow_places_by_query(
    places: list[NearbyPlace],
    query: str,
) -> list[NearbyPlace]:
    """Оставить пункты, названные в строке фильтра.

    Args:
        places: Пункты внутри радиуса.
        query: Текст поля «населённый пункт». Пустая строка не сужает список.

    Returns:
        list[NearbyPlace]: Подходящие пункты.
    """
    tokens = set(locality_tokens(query))
    if not tokens:
        return places
    matched: list[NearbyPlace] = []
    for place in places:
        folded = strip_settlement_prefix(place.name)
        token_inside = any(len(token) >= 3 and token in folded for token in tokens)
        if folded in tokens or token_inside:
            matched.append(place)
    return matched


def filter_by_locality_names(qs: QuerySet, names: list[str]) -> QuerySet:
    """Оставить объекты, чей город совпадает с одним из названий.

    Совпадение точное или по части строки: «Раменское, Жуковский» находится
    и по Раменскому, и по Жуковскому.
    """
    if not names:
        return qs.none()
    combined = Q()
    for name in names:
        combined |= Q(city__iexact=name) | Q(city__icontains=name)
    return qs.filter(combined)


def _distance_for_city(city: str, distance_by_name: dict[str, float]) -> float | None:
    """Ближайшее расстояние среди городов, перечисленных в одной строке."""
    best: float | None = None
    for token in locality_tokens(city):
        distance = distance_by_name.get(token)
        if distance is None:
            continue
        if best is None or distance < best:
            best = distance
    return best


def nearby_players(
    *,
    places: list[NearbyPlace],
    exclude_player_id: int | None,
) -> list[Player]:
    """Игроки из населённых пунктов рядом, от ближних к дальним.

    Служебные игроки bye не попадают в список. Ограничения по числу нет:
    страницу по ``NEARBY_PLAYERS_PAGE_SIZE`` выбирает вызывающий код.

    Args:
        places: Населённые пункты внутри радиуса.
        exclude_player_id: Игрок, которого не показывать (обычно зритель).

    Returns:
        list[Player]: Все подходящие игроки, у каждого заполнено ``distance_km``.
    """
    if not places:
        return []
    distance_by_name = {
        strip_settlement_prefix(place.name): place.distance_km for place in places
    }
    players = Player.objects.filter(is_bye=False).select_related("user")
    if exclude_player_id is not None:
        players = players.exclude(pk=exclude_player_id)
    players = filter_by_locality_names(players, [place.name for place in places])
    ranked: list[tuple[float, int, Player]] = []
    for player in players:
        distance = _distance_for_city(player.city, distance_by_name)
        if distance is None:
            continue
        player.distance_km = round(distance, 1)
        ranked.append((distance, player.pk, player))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [player for _, _, player in ranked]
