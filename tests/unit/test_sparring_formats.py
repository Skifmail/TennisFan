"""Разбор многозначного фильтра форматов спарринга."""

import pytest

from apps.sparring.formats import parse_sparring_formats


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([], ()),
        (["all"], ()),
        (["wrong"], ()),
        (["team", "singles", "team"], ("singles", "team")),
        (["doubles"], ("doubles",)),
        (["singles", "doubles", "team"], ()),
        (["all", "singles"], ()),
    ],
)
def test_formats_when_query_values_given_should_normalize_selection(
    raw: list[str],
    expected: tuple[str, ...],
) -> None:
    """Пустой набор означает все форматы; дубли и мусор не ломают выбор.

    Args:
        raw: Значения одноимённых параметров type.
        expected: Канонический набор либо отсутствие ограничения.
    """
    assert parse_sparring_formats(raw) == expected
