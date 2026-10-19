"""Общая лента спаррингов с приоритетом города игрока и расстояния."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from django.conf import settings
from loguru import logger

from apps.core.city_map_coordinates import (
    AmbiguousLocalityError,
    LocalityCoordinates,
    load_reference_localities,
    resolve_locality,
)
from apps.core.models import City
from apps.sparring.models import DoublesMatchRequest, SparringRequest
from apps.sparring.nearby import haversine_km

FEED_PAGE_SIZE: Final = 10
SparringFormat = Literal["singles", "doubles", "team"]


@dataclass(frozen=True)
class SparringFeedEntry:
    """Карточка общей ленты без изменения исходной заявки.

    Attributes:
        request: Одиночная, парная или командная заявка.
        sparring_type: Формат для выбора шаблона карточки.
        is_local: Совпадает ли населённый пункт с городом зрителя.
        distance_km: Расстояние до города зрителя либо отсутствие координат.
    """

    request: SparringRequest | DoublesMatchRequest
    sparring_type: SparringFormat
    is_local: bool
    distance_km: float | None


def rank_sparring_feed(
    *,
    singles: Iterable[SparringRequest],
    doubles: Iterable[DoublesMatchRequest],
    viewer_city: str,
) -> list[SparringFeedEntry]:
    """Объединяет форматы: свой город, близкие города, дальние, неизвестные.

    При равном расстоянии первыми идут новые заявки; тип и PK обеспечивают
    стабильную пагинацию. Координаты разрешаются один раз для каждого названия,
    без сетевого геокодирования и запросов к базе на каждую карточку.

    Args:
        singles: Отфильтрованные одиночные заявки.
        doubles: Отфильтрованные парные и командные заявки.
        viewer_city: Населённый пункт из профиля; пустой для гостя.

    Returns:
        Общая упорядоченная лента для последующей пагинации.
    """
    stored = [
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
    reference = load_reference_localities(
        Path(settings.BASE_DIR) / "static" / "documents" / "city.csv"
    )
    resolved: dict[str, LocalityCoordinates | None] = {}

    def resolve(name: str) -> LocalityCoordinates | None:
        if name not in resolved:
            try:
                resolved[name] = resolve_locality(name, stored, reference)
            except AmbiguousLocalityError:
                logger.warning(
                    "Неоднозначный населённый пункт ленты спаррингов", city=name
                )
                resolved[name] = None
        return resolved[name]

    origin = resolve(viewer_city) if viewer_city.strip() else None
    entries: list[SparringFeedEntry] = []

    def append(
        item: SparringRequest | DoublesMatchRequest, kind: SparringFormat
    ) -> None:
        destination = resolve(item.city)
        is_local = bool(
            origin is not None
            and destination is not None
            and origin.identity == destination.identity
        )
        distance: float | None = None
        if origin is not None and destination is not None:
            if origin.coordinates is not None and destination.coordinates is not None:
                distance = haversine_km(*origin.coordinates, *destination.coordinates)
        entries.append(SparringFeedEntry(item, kind, is_local, distance))

    for single in singles:
        append(single, "singles")
    for pair in doubles:
        append(pair, "team" if pair.kind == "team" else "doubles")
    entries.sort(
        key=lambda row: (
            not row.is_local,
            row.distance_km if row.distance_km is not None else float("inf"),
            -row.request.created_at.timestamp(),
            row.sparring_type,
            row.request.pk,
        )
    )
    return entries
