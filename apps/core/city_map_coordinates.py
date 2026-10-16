"""Однозначный выбор координат по названию, региону и типу поселения."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

from loguru import logger

from apps.core.localities import (
    ST_CITY,
    format_locality_label,
    parse_kladr_row,
    primary_locality_name,
    split_typed_name,
)

_COUNTRY_NAMES: Final = frozenset({"россия", "рф", "russia"})


class LocalityResolutionError(ValueError):
    """Базовая ошибка определения населённого пункта.

    Attributes:
        args: Описание причины ошибки.
    """


class AmbiguousLocalityError(LocalityResolutionError):
    """Название соответствует нескольким населённым пунктам.

    Attributes:
        args: Название, требующее уточнения региона.
    """


@dataclass(frozen=True)
class LocalityCoordinates:
    """Идентичность населённого пункта и его координаты.

    Attributes:
        name: Официальное название.
        region: Субъект РФ либо пустая строка для старых записей.
        settlement_type: Тип населённого пункта.
        coordinates: Пара широта/долгота либо отсутствие координат.
    """

    name: str
    region: str
    settlement_type: str
    coordinates: tuple[float, float] | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        """Возвращает ключ для сравнения и группировки.

        Returns:
            Нормализованные название, регион и тип.
        """
        return (_normalize(self.name), _normalize(self.region), self.settlement_type)

    @property
    def label(self) -> str:
        """Возвращает подпись для карты.

        Returns:
            Название с регионом и префиксом для негородских поселений.
        """
        return format_locality_label(self.name, self.settlement_type, self.region)


def _normalize(value: str) -> str:
    return value.strip().casefold().replace("ё", "е")


@lru_cache(maxsize=1)
def load_reference_localities(path: Path) -> tuple[LocalityCoordinates, ...]:
    """Загружает координаты из проверенного CSV-справочника.

    Args:
        path: Путь к CSV в формате КЛАДР.

    Returns:
        Записи с координатами; пустой кортеж при недоступном файле.
    """
    records: list[LocalityCoordinates] = []
    try:
        with path.open(encoding="utf-8") as source:
            for row in csv.DictReader(source):
                parsed = parse_kladr_row(row)
                if parsed is None or parsed["lat"] is None or parsed["lng"] is None:
                    continue
                records.append(
                    LocalityCoordinates(
                        str(parsed["name"]),
                        str(parsed["region"]),
                        str(parsed["settlement_type"]),
                        (float(parsed["lat"]), float(parsed["lng"])),
                    )
                )
    except OSError:
        logger.exception("Не удалось прочитать справочник координат", path=str(path))
    return tuple(records)


def resolve_locality(
    raw_name: str,
    stored: Sequence[LocalityCoordinates],
    reference: Sequence[LocalityCoordinates],
) -> LocalityCoordinates:
    """Выбирает поселение, не подменяя регион или тип одноимённой записью.

    Старую запись без региона заменяет единственная полная запись. CSV имеет
    приоритет над сохранёнными координатами только для той же идентичности.

    Args:
        raw_name: Значение поля города в профиле.
        stored: Записи базы, включая старые записи без региона.
        reference: Записи проверенного справочника.

    Returns:
        Однозначное поселение либо запись без координат для неизвестного места.

    Raises:
        AmbiguousLocalityError: Если без уточнения региона найдено несколько мест.
    """
    name_part, _, region_part = raw_name.strip().partition(",")
    settlement_type, name = split_typed_name(name_part)
    if settlement_type == ST_CITY:
        name = primary_locality_name(name)
    region = "" if _normalize(region_part) in _COUNTRY_NAMES else region_part.strip()
    target = LocalityCoordinates(name, region, settlement_type)
    candidates: dict[tuple[str, str, str], LocalityCoordinates] = {}
    for record in (*stored, *reference):
        if _normalize(record.name) != _normalize(name):
            continue
        if record.settlement_type != settlement_type:
            continue
        if region and _normalize(record.region) != _normalize(region):
            continue
        candidates[record.identity] = record
    if any(record.region for record in candidates.values()):
        candidates = {
            key: record for key, record in candidates.items() if record.region
        }
    if len(candidates) > 1:
        raise AmbiguousLocalityError(raw_name)
    return next(iter(candidates.values()), target)
