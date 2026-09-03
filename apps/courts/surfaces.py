"""Канонические покрытия теннисных кортов и разбор старого свободного текста."""

from __future__ import annotations

import re
from typing import Iterable

from django.db.models import QuerySet, TextChoices

_SPLIT_RE = re.compile(r"[,;/]| и ", re.IGNORECASE)
_INDOOR_PREFIX_RE = re.compile(r"крытые\s*:\s*", re.IGNORECASE)
_OUTDOOR_PREFIX_RE = re.compile(r"открытые\s*:\s*", re.IGNORECASE)


class CourtSurface(TextChoices):
    """Фиксированный набор покрытий для админки, заявок и фильтра каталога."""

    HARD = "hard", "Хард"
    CLAY = "clay", "Грунт"
    TERAFLEX = "teraflex", "Терафлекс"
    OTHER = "other", "Другое"


_TOKEN_ALIASES: dict[str, str] = {
    "hard": "hard",
    "хард": "hard",
    "clay": "clay",
    "грунт": "clay",
    "грун": "clay",
    "teraflex": "teraflex",
    "терафлекс": "teraflex",
    "трава": "other",
    "grass": "other",
    "линолеум": "other",
    "линолеум спортивный": "other",
    "другое": "other",
    "other": "other",
}

_ALIAS_BY_LENGTH: tuple[tuple[str, str], ...] = tuple(
    sorted(_TOKEN_ALIASES.items(), key=lambda item: len(item[0]), reverse=True)
)


def normalize_surface_codes(values: Iterable[str] | None) -> list[str]:
    """Оставить только известные коды покрытия без дублей.

    Args:
        values: Сырые коды из формы, JSON или миграции.

    Returns:
        list[str]: Уникальные коды в порядке ``CourtSurface``.
    """
    allowed = set(CourtSurface.values)
    present = {str(code) for code in (values or []) if str(code) in allowed}
    return [code for code in CourtSurface.values if code in present]


def format_surface_labels(codes: Iterable[str] | None) -> str:
    """Собрать подписи покрытий через запятую.

    Args:
        codes: Канонические коды покрытия.

    Returns:
        str: Например ``Хард, Грунт``.
    """
    labels = [CourtSurface(code).label for code in normalize_surface_codes(codes)]
    return ", ".join(labels)


def compose_surface_display(
    *,
    is_indoor: bool,
    indoor_surfaces: Iterable[str] | None,
    is_outdoor: bool,
    outdoor_surfaces: Iterable[str] | None,
) -> str:
    """Собрать строку покрытия для карточки и детальной страницы.

    Args:
        is_indoor: Есть крытые корты.
        indoor_surfaces: Коды покрытий крытых кортов.
        is_outdoor: Есть открытые корты.
        outdoor_surfaces: Коды покрытий открытых кортов.

    Returns:
        str: Компактная подпись или префиксы «Крытые»/«Открытые».
    """
    indoor_codes = normalize_surface_codes(indoor_surfaces) if is_indoor else []
    outdoor_codes = normalize_surface_codes(outdoor_surfaces) if is_outdoor else []
    indoor_text = format_surface_labels(indoor_codes)
    outdoor_text = format_surface_labels(outdoor_codes)
    parts: list[str] = []
    if indoor_text and outdoor_text:
        parts.append(f"Крытые: {indoor_text}")
        parts.append(f"Открытые: {outdoor_text}")
        return "; ".join(parts)
    return indoor_text or outdoor_text or ""


def parse_surface_text(raw: str | None) -> list[str]:
    """Разобрать исторический свободный текст покрытия в канонические коды.

    Args:
        raw: Строка вроде ``Хард, грунт`` или ``Крытые: Хард, грунт.``.

    Returns:
        list[str]: Уникальные коды ``CourtSurface``.
    """
    if not raw or not str(raw).strip():
        return []
    text = str(raw).lower().replace("ё", "е")
    text = _INDOOR_PREFIX_RE.sub(" ", text)
    text = _OUTDOOR_PREFIX_RE.sub(" ", text)
    found: list[str] = []
    seen: set[str] = set()

    def add(code: str) -> None:
        if code not in seen:
            seen.add(code)
            found.append(code)

    tokens = [token.strip(" .") for token in _SPLIT_RE.split(text)]
    for token in tokens:
        if not token:
            continue
        mapped = _TOKEN_ALIASES.get(token)
        if mapped:
            add(mapped)
            continue
        matched = False
        for alias, code in _ALIAS_BY_LENGTH:
            if alias in token:
                add(code)
                matched = True
        if not matched:
            add("other")
    return normalize_surface_codes(found)


def parse_split_aggregated(raw: str | None) -> tuple[list[str], list[str]]:
    """Разделить агрегированную строку на крытые и открытые покрытия.

    Args:
        raw: Значение старого поля ``surface``.

    Returns:
        tuple[list[str], list[str]]: Коды крытых и открытых кортов.
    """
    if not raw or not str(raw).strip():
        return [], []
    indoor: list[str] = []
    outdoor: list[str] = []
    for part in str(raw).split(";"):
        chunk = part.strip()
        if not chunk:
            continue
        lowered = chunk.lower()
        if lowered.startswith("крытые"):
            indoor = parse_surface_text(chunk)
        elif lowered.startswith("открытые"):
            outdoor = parse_surface_text(chunk)
    if indoor or outdoor:
        return indoor, outdoor
    return parse_surface_text(raw), []


def assign_surfaces_from_legacy(
    *,
    is_indoor: bool,
    is_outdoor: bool,
    indoor_text: str,
    outdoor_text: str,
    aggregated_text: str,
) -> tuple[list[str], list[str]]:
    """Подобрать списки покрытий для миграции со старых текстовых полей.

    Args:
        is_indoor: Корт крытый.
        is_outdoor: Корт открытый.
        indoor_text: Старое ``indoor_surface``.
        outdoor_text: Старое ``outdoor_surface``.
        aggregated_text: Старое ``surface``.

    Returns:
        tuple[list[str], list[str]]: Коды крытых и открытых покрытий.
    """
    indoor = parse_surface_text(indoor_text)
    outdoor = parse_surface_text(outdoor_text)
    if indoor or outdoor:
        return indoor, outdoor
    indoor, outdoor = parse_split_aggregated(aggregated_text)
    if indoor and not outdoor and is_outdoor and not is_indoor:
        return [], indoor
    if outdoor and not indoor and is_indoor and not is_outdoor:
        return outdoor, []
    return indoor, outdoor


def filter_courts_by_surfaces(queryset: QuerySet, codes: Iterable[str]) -> QuerySet:
    """Оставить корты, у которых есть хотя бы одно выбранное покрытие.

    Args:
        queryset: Исходный queryset кортов.
        codes: Коды из GET-параметра фильтра.

    Returns:
        QuerySet: Отфильтрованный queryset.
    """
    selected = set(normalize_surface_codes(codes))
    if not selected:
        return queryset
    matching_ids: list[int] = []
    for pk, indoor, outdoor in queryset.values_list(
        "pk", "indoor_surfaces", "outdoor_surfaces"
    ):
        present = set(indoor or []) | set(outdoor or [])
        if selected & present:
            matching_ids.append(pk)
    return queryset.filter(pk__in=matching_ids)
