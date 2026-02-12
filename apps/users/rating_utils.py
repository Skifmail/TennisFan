"""
Utility functions for mapping NTRP levels to starting points and skill levels.

Starting points table (per plan):
    1.0–1.4  →  500
    1.5–1.8  →  750
    1.9–2.0  → 1000
    2.1–2.5  → 1250
    2.6–3.0  → 1500
    3.1–3.5  → 1750
    3.6–4.0  → 2000
    4.1–4.5  → 2250
    4.6–5.0  → 2500
    5.1–5.5  → 2750
    5.6–6.0  → 3000
    6.1–6.5  → 3250
    6.6–7.0  → 3500
"""

from decimal import Decimal
from typing import NamedTuple

from .models import SkillLevel


class NtrpBracket(NamedTuple):
    """Single row of the NTRP → starting-points lookup table."""

    min_level: Decimal
    max_level: Decimal
    starting_points: int


# Ordered from lowest to highest.
NTRP_STARTING_POINTS: list[NtrpBracket] = [
    NtrpBracket(Decimal("1.0"), Decimal("1.4"), 500),
    NtrpBracket(Decimal("1.5"), Decimal("1.8"), 750),
    NtrpBracket(Decimal("1.9"), Decimal("2.0"), 1000),
    NtrpBracket(Decimal("2.1"), Decimal("2.5"), 1250),
    NtrpBracket(Decimal("2.6"), Decimal("3.0"), 1500),
    NtrpBracket(Decimal("3.1"), Decimal("3.5"), 1750),
    NtrpBracket(Decimal("3.6"), Decimal("4.0"), 2000),
    NtrpBracket(Decimal("4.1"), Decimal("4.5"), 2250),
    NtrpBracket(Decimal("4.6"), Decimal("5.0"), 2500),
    NtrpBracket(Decimal("5.1"), Decimal("5.5"), 2750),
    NtrpBracket(Decimal("5.6"), Decimal("6.0"), 3000),
    NtrpBracket(Decimal("6.1"), Decimal("6.5"), 3250),
    NtrpBracket(Decimal("6.6"), Decimal("7.0"), 3500),
]


def get_starting_points(ntrp_level: Decimal) -> int:
    """Return starting rating points for a given NTRP level.

    Args:
        ntrp_level: Decimal NTRP value in range [1.0, 7.0].

    Returns:
        Starting points as an integer.

    Raises:
        ValueError: If ntrp_level is out of the valid range.
    """
    level = Decimal(str(ntrp_level))
    for bracket in NTRP_STARTING_POINTS:
        if bracket.min_level <= level <= bracket.max_level:
            return bracket.starting_points
    raise ValueError(f"NTRP level {ntrp_level} is out of valid range [1.0, 7.0].")


def map_ntrp_to_skill_level(level: Decimal) -> str:
    """Map NTRP decimal level to SkillLevel category.

    Ranges:
        1.0–2.0 → Новичок
        2.1–3.5 → Любитель
        3.6–5.0 → Опытный
        5.1–6.0 → Продвинутый
        6.1–7.0 → Профессионал
    """
    if level <= Decimal("2.0"):
        return SkillLevel.NOVICE
    if level <= Decimal("3.5"):
        return SkillLevel.AMATEUR
    if level <= Decimal("5.0"):
        return SkillLevel.EXPERIENCED
    if level <= Decimal("6.0"):
        return SkillLevel.ADVANCED
    return SkillLevel.PROFESSIONAL


def rating_to_ntrp_level(rating: int | float) -> Decimal:
    """Convert rating points back to NTRP level (inverse of get_starting_points).

    Uses the starting points table to determine the NTRP level range,
    then interpolates within that range.

    Args:
        rating: Current rating points.

    Returns:
        NTRP level as Decimal in range [1.0, 7.0].
    """
    rating_val = Decimal(str(rating))

    # Handle edge cases: below minimum or above maximum
    min_points = Decimal("500")
    max_points = Decimal("3500")
    
    if rating_val < min_points:
        # Below minimum: extrapolate linearly from 1.0
        # Assume 0 points = NTRP 0.0, 500 points = NTRP 1.0
        ntrp = Decimal("1.0") + (rating_val - min_points) / Decimal("500")
        ntrp = max(Decimal("0.0"), ntrp)
        return Decimal(str(round(float(ntrp), 1)))

    if rating_val >= max_points:
        # Above maximum: extrapolate linearly from 7.0
        # Assume 3500 points = NTRP 7.0, 4000 points = NTRP 8.0 (cap at 7.0)
        ntrp = Decimal("7.0") + (rating_val - max_points) / Decimal("500")
        ntrp = min(Decimal("7.0"), ntrp)
        return Decimal(str(round(float(ntrp), 1)))

    # Find the bracket that contains this rating
    for i, bracket in enumerate(NTRP_STARTING_POINTS):
        bracket_points = Decimal(str(bracket.starting_points))
        if rating_val < bracket_points:
            # Rating falls between previous bracket and current one
            if i == 0:
                # Below first bracket: use first bracket's range
                prev_bracket = bracket
                next_bracket = bracket
            else:
                prev_bracket = NTRP_STARTING_POINTS[i - 1]
                next_bracket = bracket

            # Interpolate NTRP level between brackets
            prev_points = Decimal(str(prev_bracket.starting_points))
            next_points = Decimal(str(next_bracket.starting_points))
            rating_range = next_points - prev_points
            ntrp_range = next_bracket.max_level - prev_bracket.min_level
            rating_offset = rating_val - prev_points

            if rating_range > 0:
                ntrp_offset = (rating_offset / rating_range) * ntrp_range
                ntrp = prev_bracket.min_level + ntrp_offset
            else:
                ntrp = prev_bracket.min_level

            return Decimal(str(round(float(ntrp), 1)))

    # Rating is >= last bracket's starting points (3500)
    # Use last bracket
    last_bracket = NTRP_STARTING_POINTS[-1]
    return last_bracket.max_level


def rating_to_skill_level(rating: int | float) -> str:
    """Convert rating points directly to SkillLevel category.

    This is a convenience function that combines rating_to_ntrp_level
    and map_ntrp_to_skill_level.

    Args:
        rating: Current rating points.

    Returns:
        SkillLevel category string.
    """
    ntrp = rating_to_ntrp_level(rating)
    return map_ntrp_to_skill_level(ntrp)
