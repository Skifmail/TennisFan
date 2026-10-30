"""Проверки возрастных границ и детских форматов счёта."""

from datetime import date

import pytest
from django.core.exceptions import ValidationError

from apps.juniors.rules import ScoreFormat, age_on, validate_score


def test_age_boundary() -> None:
    assert age_on(date(2016, 10, 31), date(2026, 10, 30)) == 9
    assert age_on(date(2016, 10, 31), date(2026, 10, 31)) == 10


@pytest.mark.parametrize(
    "format_,score,winner",
    [
        (ScoreFormat.TIEBREAK_7, [[9, 7]], 1),
        (ScoreFormat.TIEBREAK_10, [[8, 10]], 2),
        (ScoreFormat.THREE_TIEBREAKS, [[7, 5], [3, 7], [8, 6]], 1),
        (ScoreFormat.SHORT_SET, [[5, 4]], 1),
        (ScoreFormat.REGULAR_SET, [[6, 4]], 1),
    ],
)
def test_completed_scores(format_: str, score: list[list[int]], winner: int) -> None:
    assert validate_score(format_, score) == winner


@pytest.mark.parametrize(
    "format_,score",
    [
        (ScoreFormat.TIEBREAK_7, [[7, 6]]),
        (ScoreFormat.TIEBREAK_7, [[8, 4]]),
        (ScoreFormat.THREE_TIEBREAKS, [[7, 0]]),
        (ScoreFormat.THREE_TIEBREAKS, [[7, 0], [7, 0], [7, 0]]),
        (ScoreFormat.SHORT_SET, [[4, 3]]),
        (ScoreFormat.REGULAR_SET, [[8, 6]]),
        (ScoreFormat.TIEBREAK_7, [[True, 0]]),
    ],
)
def test_reject_invalid_scores(format_: str, score: list[list[int]]) -> None:
    with pytest.raises(ValidationError):
        validate_score(format_, score)
