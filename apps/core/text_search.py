"""
Вспомогательные фильтры ORM для регистронезависимого поиска по тексту.

У SQLite ``LOWER()`` и ``LIKE`` без учёта регистра по сути работают для ASCII;
для кириллицы ``Lower(поле)`` не меняет «Москва», и условие вида
``contains 'моск'`` не срабатывает. Для SQLite строим OR из ``icontains`` по
нескольким вариантам регистра строки (через ``str.capitalize()`` и т.д.).
Для PostgreSQL сохраняем ``Lower(поле)`` + подстроку в нижнем регистре.
"""

from __future__ import annotations

from django.db import connection
from django.db.models import Q, QuerySet
from django.db.models.functions import Lower


def _case_variants_for_search(s: str) -> frozenset[str]:
    """Возвращает набор вариантов строки для OR-фильтра ``icontains`` в SQLite."""
    parts = (s, s.lower(), s.upper(), s.capitalize(), s.title(), s.swapcase())
    return frozenset(p for p in parts if p)


def filter_field_contains_ci(
    qs: QuerySet,
    field: str,
    raw: str,
    *,
    annotation: str,
) -> QuerySet:
    """
    Ограничивает queryset подстрокой в поле ``field`` без учёта регистра.

    Args:
        qs: Исходный QuerySet.
        field: Имя текстового поля модели (например, ``city``).
        raw: Подстрока поиска (как ввёл пользователь).
        annotation: Уникальное имя аннотации ``Lower(field)`` для PostgreSQL
            (без конфликта с другими ``annotate`` на той же цепочке).

    Returns:
        Исходный ``qs``, если после ``strip`` строка пустая; иначе отфильтрованный
        queryset.
    """
    needle = (raw or "").strip()
    if not needle:
        return qs

    if connection.vendor == "sqlite":
        combined = Q()
        for variant in _case_variants_for_search(needle):
            combined |= Q(**{f"{field}__icontains": variant})
        return qs.filter(combined)

    return qs.annotate(**{annotation: Lower(field)}).filter(
        **{f"{annotation}__contains": needle.lower()}
    )
