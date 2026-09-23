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

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from .models import SkillLevel

# Сила хранится и показывается до сотых. Лишние знаки отбрасываются:
# 2.858 → 2.85, а не 2.86 и не 2.9.
NTRP_STEP = Decimal("0.01")
NTRP_MIN = Decimal("1.5")
NTRP_MAX = Decimal("7.0")


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
        1.5–2.4 → Новичок
        2.5–3.4 → Любитель
        3.5–4.4 → Опытный
        4.5–5.4 → Мастерс
        5.5–7.0 → Профессионал
    """

    def _choice_value(x) -> str:
        return str(x[0]) if isinstance(x, tuple) else str(x)

    # Clamp level to valid range [1.5, 7.0]
    level = max(Decimal("1.5"), min(Decimal("7.0"), level))

    if level <= Decimal("2.4"):
        return _choice_value(SkillLevel.NOVICE)
    if level <= Decimal("3.4"):
        return _choice_value(SkillLevel.AMATEUR)
    if level <= Decimal("4.4"):
        return _choice_value(SkillLevel.EXPERIENCED)
    if level <= Decimal("5.4"):
        return _choice_value(SkillLevel.ADVANCED)
    return _choice_value(SkillLevel.PROFESSIONAL)


def rating_to_ntrp_level(rating: int | float | Decimal) -> Decimal:
    """Перевести очки FAN в уровень силы.

    Линейно: сила = очки / 1000. Результат обрезается до сотых
    без округления вверх и зажимается в диапазон [1.5, 7.0].

    Args:
        rating: Текущие очки рейтинга.

    Returns:
        Уровень силы с двумя знаками после запятой.
    """
    ntrp = Decimal(str(rating)) / Decimal("1000")
    ntrp = max(NTRP_MIN, min(NTRP_MAX, ntrp))
    return ntrp.quantize(NTRP_STEP, rounding=ROUND_DOWN)


def ntrp_category_band(level: Decimal | int | float) -> Decimal:
    """Свести силу к десятым для категории (Новичок, Любитель и т.д.).

    Категории по-прежнему режутся по десятым: 2.44 остаётся в диапазоне «до 2.4».
    Отображаемая сила при этом хранится до сотых.

    Args:
        level: Уровень силы.

    Returns:
        Значение, округлённое до одного знака половиной вверх.
    """
    return Decimal(str(level)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def rating_to_skill_level(rating: int | float) -> str:
    """Convert rating points directly to SkillLevel category.

    This is a convenience function that combines rating_to_ntrp_level
    and map_ntrp_to_skill_level.

    Args:
        rating: Current rating points.

    Returns:
        SkillLevel category string.
    """
    return map_ntrp_to_skill_level(ntrp_category_band(rating_to_ntrp_level(rating)))
