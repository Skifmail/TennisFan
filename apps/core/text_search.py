"""
Вспомогательные фильтры ORM для регистронезависимого поиска по тексту.

У SQLite ``LOWER()`` и ``LIKE`` без учёта регистра по сути работают для ASCII;
для кириллицы ``Lower(поле)`` не меняет «Москва», и условие вида
``contains 'моск'`` не срабатывает. Для SQLite строим OR из ``icontains`` по
нескольким вариантам регистра строки (через ``str.capitalize()`` и т.д.).
Для PostgreSQL сохраняем ``Lower(поле)`` + подстроку в нижнем регистре.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.db import connection
from django.db.models import Q, QuerySet
from django.db.models.functions import Lower


def _case_variants_for_search(s: str) -> frozenset[str]:
    """Возвращает набор вариантов строки для OR-фильтра ``icontains`` в SQLite."""
    parts = (s, s.lower(), s.upper(), s.capitalize(), s.title(), s.swapcase())
    return frozenset(p for p in parts if p)


def build_contains_all_words_q(raw: str, fields: Sequence[str]) -> Q:
    """Построить условие: каждое слово запроса встречается в одном из полей.

    Поиск по отдельным словам позволяет находить игрока по полному имени,
    когда имя и фамилия хранятся в разных полях, в любом порядке и с лишними
    пробелами. Для SQLite перебираются варианты регистра, чтобы кириллица
    сравнивалась без учёта регистра.

    Args:
        raw: Строка поиска, введённая пользователем.
        fields: Поля модели для поиска (например, ``user__first_name``).

    Returns:
        ``Q`` с условием по всем словам или пустой ``Q``, если слов нет.
    """
    combined = Q()
    for word in (raw or "").split():
        word_q = Q()
        for variant in _case_variants_for_search(word):
            for field in fields:
                word_q |= Q(**{f"{field}__icontains": variant})
        combined &= word_q
    return combined


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
