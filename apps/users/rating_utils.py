"""
Utility functions for mapping NTRP levels to starting points and skill levels.

Starting points table (per plan):
    1.0–1.4  → 1000
    1.5–1.8  → 1500
    1.9–2.0  → 2000
    2.1–2.5  → 2500
    2.6–3.0  → 3000
    3.1–3.5  → 3500
    3.6–4.0  → 4000
    4.1–4.5  → 4500
    4.6–5.0  → 5000
    5.1–5.5  → 5500
    5.6–6.0  → 6000
    6.1–6.5  → 6500
    6.6–7.0  → 7000
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
    NtrpBracket(Decimal("1.0"), Decimal("1.4"), 1000),
    NtrpBracket(Decimal("1.5"), Decimal("1.8"), 1500),
    NtrpBracket(Decimal("1.9"), Decimal("2.0"), 2000),
    NtrpBracket(Decimal("2.1"), Decimal("2.5"), 2500),
    NtrpBracket(Decimal("2.6"), Decimal("3.0"), 3000),
    NtrpBracket(Decimal("3.1"), Decimal("3.5"), 3500),
    NtrpBracket(Decimal("3.6"), Decimal("4.0"), 4000),
    NtrpBracket(Decimal("4.1"), Decimal("4.5"), 4500),
    NtrpBracket(Decimal("4.6"), Decimal("5.0"), 5000),
    NtrpBracket(Decimal("5.1"), Decimal("5.5"), 5500),
    NtrpBracket(Decimal("5.6"), Decimal("6.0"), 6000),
    NtrpBracket(Decimal("6.1"), Decimal("6.5"), 6500),
    NtrpBracket(Decimal("6.6"), Decimal("7.0"), 7000),
]


def get_starting_points(ntrp_level: Decimal) -> int:
    """Return starting rating points for a given NTRP level.

    Interpolates to ensure that rating_to_ntrp_level returns the same NTRP level.

    The function matches the interpolation logic used in rating_to_ntrp_level:
    - rating_to_ntrp_level uses: ntrp = prev_bracket.min_level + (points - prev_points) / (next_points - prev_points) * (next_bracket.max_level - prev_bracket.min_level)
    - For levels within a bracket, we interpolate between current bracket and next bracket
    - Reverse formula: points = current_points + (level - bracket.min_level) / (next_bracket.max_level - bracket.min_level) * (next_points - current_points)

    Args:
        ntrp_level: Decimal NTRP value in range [1.0, 7.0].

    Returns:
        Starting points as an integer (interpolated to match rating_to_ntrp_level).

    Raises:
        ValueError: If ntrp_level is out of the valid range.
    """
    level = Decimal(str(ntrp_level))

    # Find the bracket that contains this NTRP level
    for i, bracket in enumerate(NTRP_STARTING_POINTS):
        if bracket.min_level <= level <= bracket.max_level:
            # If level is at the minimum of the bracket, return bracket's starting points
            if level == bracket.min_level:
                return bracket.starting_points

            # For levels within the bracket, interpolate between current bracket and next bracket
            if i < len(NTRP_STARTING_POINTS) - 1:
                next_bracket = NTRP_STARTING_POINTS[i + 1]
                current_points = Decimal(str(bracket.starting_points))
                next_points = Decimal(str(next_bracket.starting_points))

                # rating_to_ntrp_level interpolates between bracket and next_bracket
                # For points between current_points and next_points, it interpolates between
                # bracket.min_level and next_bracket.max_level
                # But actually, it uses prev_bracket.min_level and next_bracket.max_level
                # So for level within bracket, we need to interpolate between bracket and next_bracket
                # using bracket.min_level as start and next_bracket.max_level as end

                ntrp_range = next_bracket.max_level - bracket.min_level
                if ntrp_range > 0:
                    ntrp_offset = level - bracket.min_level
                    position = ntrp_offset / ntrp_range
                    points_range = next_points - current_points
                    interpolated_points = current_points + (position * points_range)
                    return int(round(interpolated_points))

            return bracket.starting_points

    raise ValueError(f"NTRP level {ntrp_level} is out of valid range [1.0, 7.0].")


def map_ntrp_to_skill_level(level: Decimal) -> str:
    """Map NTRP decimal level to SkillLevel category.

    Ranges:
        1.0–2.0 → Новичок
        2.1–3.5 → Любитель
        3.6–5.0 → Опытный
        5.1–6.0 → Мастерс
        6.1–7.0 → Профессионал
    """

    def _choice_value(x) -> str:
        return str(x[0]) if isinstance(x, tuple) else str(x)

    if level <= Decimal("2.0"):
        return _choice_value(SkillLevel.NOVICE)
    if level <= Decimal("3.5"):
        return _choice_value(SkillLevel.AMATEUR)
    if level <= Decimal("5.0"):
        return _choice_value(SkillLevel.EXPERIENCED)
    if level <= Decimal("6.0"):
        return _choice_value(SkillLevel.ADVANCED)
    return _choice_value(SkillLevel.PROFESSIONAL)


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
    min_points = Decimal("1000")
    max_points = Decimal("7000")

    if rating_val < min_points:
        # Below minimum: extrapolate linearly from 1.0
        # Assume 0 points = NTRP 0.0, 1000 points = NTRP 1.0
        ntrp = Decimal("1.0") + (rating_val - min_points) / Decimal("1000")
        ntrp = max(Decimal("0.0"), ntrp)
        return Decimal(str(round(float(ntrp), 1)))

    if rating_val >= max_points:
        # Above maximum: extrapolate linearly from 7.0
        # Assume 7000 points = NTRP 7.0, 8000 points = NTRP 8.0 (cap at 7.0)
        ntrp = Decimal("7.0") + (rating_val - max_points) / Decimal("1000")
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

    # Rating is >= last bracket's starting points (7000)
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
