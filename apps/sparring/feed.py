"""Общая лента спаррингов с приоритетом города игрока и расстояния."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

from apps.core.locality_proximity import LocalityProximityIndex
from apps.sparring.models import DoublesMatchRequest, SparringRequest

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
    index = LocalityProximityIndex(viewer_city)
    entries: list[SparringFeedEntry] = []

    def append(
        item: SparringRequest | DoublesMatchRequest, kind: SparringFormat
    ) -> None:
        proximity = index.get(item.city)
        entries.append(
            SparringFeedEntry(item, kind, proximity.is_local, proximity.distance_km)
        )

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
