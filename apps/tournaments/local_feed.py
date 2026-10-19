"""Географическая сортировка публичной ленты турниров."""

from collections.abc import Iterable
from typing import Protocol, cast

from apps.core.locality_proximity import LocalityProximityIndex
from apps.tournaments.models import Tournament


class _TournamentCardLocation(Protocol):
    """Временные географические поля карточки, не сохраняемые в модели.

    Attributes:
        is_local: Принадлежит ли турнир городу игрока.
        distance_km: Расстояние до города турнира.
    """

    is_local: bool
    distance_km: float | None


def rank_tournaments_near_player(
    tournaments: Iterable[Tournament],
    *,
    viewer_city: str,
) -> list[Tournament]:
    """Ставит свой город первым, остальные — по возрастанию расстояния.

    Стабильная сортировка сохраняет исходный порядок статусов и дат внутри
    одного города. Неизвестные координаты не исключают турнир из выдачи.

    Args:
        tournaments: Турниры, уже отфильтрованные и упорядоченные по статусу.
        viewer_city: Населённый пункт зрителя.

    Returns:
        Турниры с признаками is_local и distance_km для шаблона.
    """
    index = LocalityProximityIndex(viewer_city)
    ranked = []
    for position, tournament in enumerate(tournaments):
        proximity = index.get(tournament.city)
        card = cast(_TournamentCardLocation, tournament)
        card.is_local = proximity.is_local
        card.distance_km = proximity.distance_km
        ranked.append(
            (
                not proximity.is_local,
                (
                    proximity.distance_km
                    if proximity.distance_km is not None
                    else float("inf")
                ),
                position,
                tournament,
            )
        )
    ranked.sort(key=lambda row: row[:3])
    return [row[3] for row in ranked]
