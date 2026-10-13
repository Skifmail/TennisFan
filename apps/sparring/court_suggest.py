"""Подсказки кортов для поля «Предпочтительное место» в заявке на спарринг."""

from __future__ import annotations

from apps.core.localities import locality_tokens
from apps.core.sports import sport_code
from apps.courts.models import Court
from apps.courts.surfaces import filter_courts_by_venue

SUGGEST_LIMIT = 12
MIN_QUERY_LENGTH = 2


def _fold(value: str) -> str:
    """Свернуть строку для сравнения без регистра и лишних пробелов."""
    return " ".join((value or "").split()).casefold().replace("ё", "е")


def suggest_sparring_courts(
    *,
    query: str,
    city: str,
    sport: str,
) -> list[dict[str, str]]:
    """Найти активные корты для автодополнения места спарринга.

    Если населённый пункт уже выбран, без текста запроса возвращаются корты
    этого пункта. Текст от двух символов сужает список по названию, адресу
    и району. Без города короткий запрос ничего не возвращает.

    Args:
        query: То, что игрок уже ввёл в поле места.
        city: Населённый пункт из соседнего поля формы.
        sport: ``tennis`` или ``padel``. Площадка «оба» подходит к любому.

    Returns:
        list[dict[str, str]]: Не больше ``SUGGEST_LIMIT`` кортов с полями
        ``name``, ``city`` и ``address``.
    """
    needle = _fold(query)
    city_tokens = set(locality_tokens(city))
    if len(needle) < MIN_QUERY_LENGTH and not city_tokens:
        return []
    courts = filter_courts_by_venue(
        Court.objects.filter(is_active=True).only(
            "name", "city", "address", "district"
        ),
        sport_code(sport or "tennis"),
    )
    matched: list[Court] = []
    for court in courts:
        if city_tokens and not city_tokens.intersection(locality_tokens(court.city)):
            continue
        haystack = " ".join(
            (
                _fold(court.name),
                _fold(court.address),
                _fold(court.district),
            )
        )
        if needle and needle not in haystack:
            continue
        matched.append(court)
    matched.sort(key=lambda court: (_fold(court.name), _fold(court.city)))
    return [
        {
            "name": court.name,
            "city": court.city,
            "address": court.address,
        }
        for court in matched[:SUGGEST_LIMIT]
    ]
