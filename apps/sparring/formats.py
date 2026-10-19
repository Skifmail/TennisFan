"""Канонический выбор одного или нескольких форматов спарринга."""

from collections.abc import Sequence
from typing import Final

SPARRING_FORMAT_OPTIONS: Final = (
    ("singles", "Одиночный 1×1"),
    ("doubles", "Парный 2×2"),
    ("team", "Командный"),
)


def parse_sparring_formats(values: Sequence[str]) -> tuple[str, ...]:
    """Разбирает повторяющиеся query-параметры type.

    Args:
        values: Значения из GET.getlist("type").

    Returns:
        Канонический набор без дублей; пустой кортеж означает все форматы.
    """
    selected = tuple(code for code, _ in SPARRING_FORMAT_OPTIONS if code in values)
    if "all" in values or len(selected) == len(SPARRING_FORMAT_OPTIONS):
        return ()
    return selected
