"""Общий расчёт близости населённых пунктов для лент событий."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from loguru import logger

from apps.core.city_map_coordinates import (
    AmbiguousLocalityError,
    LocalityCoordinates,
    load_reference_localities,
    resolve_locality,
)
from apps.core.distances import haversine_km
from apps.core.models import City


@dataclass(frozen=True)
class LocalityProximity:
    """Близость населённого пункта относительно города профиля.

    Attributes:
        is_local: Совпадает ли полная идентичность населённых пунктов.
        distance_km: Расстояние между центрами или отсутствие координат.
    """

    is_local: bool
    distance_km: float | None


class LocalityProximityIndex:
    """Разрешает населённые пункты по одному снимку справочника на запрос.

    Attributes:
        _stored: Записи базы с сохранением региона и типа.
        _reference: Проверенный CSV-справочник.
        _resolved: Кэш разрешённых названий без сетевого геокодирования.
        _origin: Населённый пункт зрителя либо отсутствие точки отсчёта.
    """

    def __init__(self, viewer_city: str) -> None:
        """Загружает справочник и определяет точку отсчёта.

        Args:
            viewer_city: Населённый пункт профиля или пустая строка для гостя.
        """
        self._stored = [
            LocalityCoordinates(
                row.name,
                row.region,
                row.settlement_type,
                (
                    (float(row.lat), float(row.lng))
                    if row.lat is not None and row.lng is not None
                    else None
                ),
            )
            for row in City.objects.all()
        ]
        self._reference = load_reference_localities(
            Path(settings.BASE_DIR) / "static" / "documents" / "city.csv"
        )
        self._resolved: dict[str, LocalityCoordinates | None] = {}
        self._origin = self._resolve(viewer_city) if viewer_city.strip() else None

    def _resolve(self, name: str) -> LocalityCoordinates | None:
        if name not in self._resolved:
            try:
                self._resolved[name] = resolve_locality(
                    name, self._stored, self._reference
                )
            except AmbiguousLocalityError:
                logger.warning("Неоднозначный населённый пункт ленты", city=name)
                self._resolved[name] = None
        return self._resolved[name]

    def get(self, city: str) -> LocalityProximity:
        """Вычисляет близость без дополнительного запроса к базе.

        Args:
            city: Населённый пункт события.

        Returns:
            Признак своего города и расстояние; неизвестное расстояние — None.
        """
        destination = self._resolve(city)
        origin = self._origin
        is_local = bool(
            origin and destination and origin.identity == destination.identity
        )
        distance = None
        if origin and destination and origin.coordinates and destination.coordinates:
            distance = haversine_km(*origin.coordinates, *destination.coordinates)
        return LocalityProximity(is_local, distance)
