"""Поиск корта для формы клубного турнира."""

from django.db.models import QuerySet

from apps.core.sports import Sport, sport_code
from apps.courts.models import Court
from apps.courts.surfaces import filter_courts_by_venue

SEARCH_LIMIT = 12
MIN_QUERY_LENGTH = 2


def active_courts() -> QuerySet[Court]:
    """Активные корты каталога, из которых можно выбрать площадку турнира.

    Returns:
        QuerySet[Court]: Корты с ``is_active=True``, по городу и названию.
    """
    return Court.objects.filter(is_active=True).order_by("city", "name")


def search_tournament_courts(query: str, sport: str) -> list[Court]:
    """Найти корты по названию, городу или адресу с учётом вида спорта.

    Args:
        query: Строка поиска. Короче двух символов результат пустой.
        sport: ``tennis`` или ``padel``. Площадка «оба» подходит к любому.

    Returns:
        list[Court]: Не больше ``SEARCH_LIMIT`` кортов.
    """
    needle = " ".join((query or "").split()).casefold()
    if len(needle) < MIN_QUERY_LENGTH:
        return []
    candidates = filter_courts_by_venue(
        active_courts(),
        sport_code(sport or Sport.TENNIS),
    )
    matched = [
        court
        for court in candidates
        if needle in court.name.casefold()
        or needle in court.city.casefold()
        or needle in (court.address or "").casefold()
    ]
    matched.sort(key=lambda court: (court.city.casefold(), court.name.casefold()))
    return matched[:SEARCH_LIMIT]
