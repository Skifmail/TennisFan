"""Виды спорта платформы.

Существующие записи без явного вида спорта считаются теннисом.
Падел использует те же турниры, спарринги и тренировки, но отдельный рейтинг.
"""

from __future__ import annotations

from django.db import models


class Sport(models.TextChoices):
    """Вид спорта события или профиля силы."""

    TENNIS = "tennis", "Теннис"
    PADEL = "padel", "Падел"


class VenueSport(models.TextChoices):
    """Какие виды спорта принимает площадка."""

    TENNIS = "tennis", "Теннис"
    PADEL = "padel", "Падел"
    BOTH = "both", "Теннис и падел"


SportArg = str | tuple[str, str]


def sport_code(value: SportArg | None, *, default: str = "tennis") -> str:
    """Привести строку или член TextChoices к коду вида спорта.

    mypy без django-stubs видит ``Sport.TENNIS`` как кортеж ``(value, label)``.
    В рантайме член ``TextChoices`` уже строка, поэтому ветка кортежа — запасная.

    Args:
        value: Код, член ``Sport`` или пустое значение.
        default: Код, если значение пустое.

    Returns:
        str: ``tennis`` или ``padel``.
    """
    if value is None or value == "":
        return default
    if isinstance(value, tuple):
        return value[0]
    return value


def parse_sport_filter(raw: str | None, *, default: str = "tennis") -> str:
    """Разобрать фильтр вида спорта из query-параметра.

    Пустое или неизвестное значение не смешивает ленты: остаётся теннис.
    ``all`` показывает оба вида спорта.

    Args:
        raw: Значение параметра ``sport``.
        default: Вид спорта, если параметр пустой или неизвестный.

    Returns:
        str: ``tennis``, ``padel`` или ``all``.
    """
    value = (raw or "").strip().lower()
    if value == "all":
        return "all"
    if value in Sport.values:
        return value
    return default
