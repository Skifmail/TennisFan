"""
Utility functions for mapping strength levels (1.5-7.0) to starting points and skill levels.

Linear mapping: strength * 1000 = FAN points
    Сила 1.5  → 1500 FAN points
    Сила 2.0  → 2000 FAN points
    Сила 2.5  → 2500 FAN points
    ...
    Сила 7.0  → 7000 FAN points

This provides a stable, predictable mapping without jumps or interpolation issues.
"""

from decimal import Decimal

from .models import SkillLevel


def get_starting_points(ntrp_level: Decimal) -> int:
    """Return starting rating points for a given strength level.

    Uses linear mapping: FAN points = strength * 1000

    Args:
        ntrp_level: Decimal strength value in range [1.5, 7.0].

    Returns:
        Starting points as an integer.

    Raises:
        ValueError: If ntrp_level is out of the valid range.
    """
    level = Decimal(str(ntrp_level))

    if level < Decimal("1.5") or level > Decimal("7.0"):
        raise ValueError(f"Level {ntrp_level} is out of valid range [1.5, 7.0].")

    # Linear mapping: strength * 1000 = FAN points
    points = level * Decimal("1000")
    return int(round(points))


def map_ntrp_to_skill_level(level: Decimal) -> str:
    """Map strength decimal level to SkillLevel category.

    Ranges:
        1.5–2.5 → Новичок
        2.6–3.5 → Любитель
        3.6–4.5 → Опытный
        4.6–5.5 → Мастерс
        5.6–7.0 → Профессионал
    """

    def _choice_value(x) -> str:
        return str(x[0]) if isinstance(x, tuple) else str(x)

    # Clamp level to valid range [1.5, 7.0]
    level = max(Decimal("1.5"), min(Decimal("7.0"), level))

    if level <= Decimal("2.5"):
        return _choice_value(SkillLevel.NOVICE)
    if level <= Decimal("3.5"):
        return _choice_value(SkillLevel.AMATEUR)
    if level <= Decimal("4.5"):
        return _choice_value(SkillLevel.EXPERIENCED)
    if level <= Decimal("5.5"):
        return _choice_value(SkillLevel.ADVANCED)
    return _choice_value(SkillLevel.PROFESSIONAL)


def rating_to_ntrp_level(rating: int | float) -> Decimal:
    """Convert rating points back to strength level (inverse of get_starting_points).

    Uses linear mapping: strength = FAN points / 1000

    Args:
        rating: Current rating points.

    Returns:
        Strength level as Decimal in range [1.5, 7.0] (clamped).
    """
    rating_val = Decimal(str(rating))

    # Linear mapping: strength = FAN points / 1000
    ntrp = rating_val / Decimal("1000")

    # Clamp to valid range [1.5, 7.0]
    ntrp = max(Decimal("1.5"), min(Decimal("7.0"), ntrp))

    return Decimal(str(round(float(ntrp), 1)))


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
