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


def sport_codes_from_venue(value: str | None) -> list[str]:
    """Разложить ``tennis`` / ``padel`` / ``both`` на коды видов спорта.

    Args:
        value: Значение ``VenueSport`` или пусто.

    Returns:
        list[str]: ``tennis`` и/или ``padel``. Пустое считается теннисом.
    """
    tennis = sport_code(Sport.TENNIS)
    padel = sport_code(Sport.PADEL)
    both = sport_code(VenueSport.BOTH)
    code = (value or "").strip() or tennis
    if code == both:
        return [tennis, padel]
    if code == padel:
        return [padel]
    return [tennis]


def venue_sport_from_codes(codes: list[str] | tuple[str, ...] | None) -> str:
    """Собрать ``VenueSport`` из отмеченных чекбоксов теннис/падел.

    Args:
        codes: Выбранные коды ``Sport``.

    Returns:
        str: ``tennis``, ``padel`` или ``both``. Пустой набор — теннис.
    """
    tennis = sport_code(Sport.TENNIS)
    padel = sport_code(Sport.PADEL)
    selected = {sport_code(item) for item in (codes or []) if item}
    has_tennis = tennis in selected
    has_padel = padel in selected
    if has_tennis and has_padel:
        return sport_code(VenueSport.BOTH)
    if has_padel:
        return padel
    return tennis


def venue_sport_labels(value: str | None) -> list[str]:
    """Подписи видов спорта для бейджей на карточках.

    Args:
        value: Значение ``VenueSport``.

    Returns:
        list[str]: «Теннис» и/или «Падел».
    """
    return [str(Sport(code).label) for code in sport_codes_from_venue(value)]


def venue_sport_catalog_values(sport_filter: str) -> tuple[str, ...] | None:
    """Значения поля ``sport``, попадающие в каталог выбранного вида.

    Запись «теннис и падел» видна в обеих лентах. ``all`` не фильтрует.

    Args:
        sport_filter: ``tennis``, ``padel`` или ``all``.

    Returns:
        tuple[str, ...] | None: Коды для ``__in`` либо ``None``.
    """
    if sport_filter == "all":
        return None
    if sport_filter == sport_code(Sport.PADEL):
        return (
            sport_code(VenueSport.PADEL),
            sport_code(VenueSport.BOTH),
        )
    return (
        sport_code(VenueSport.TENNIS),
        sport_code(VenueSport.BOTH),
        "",
    )
