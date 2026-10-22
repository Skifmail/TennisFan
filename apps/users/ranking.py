"""Рейтинговый балл игроков для главной страницы и страницы «Рейтинг».

FAN-рейтинг силы показывает уровень игры и не подходит для публичного
турнирного рейтинга: новичок с высоким стартовым уровнем оказался бы выше
игроков с победами, не сыграв ни одного матча. Поэтому позиция в рейтинге
считается отдельным баллом, в котором главное — реальные результаты.

Формула::

    Балл = 100 × Победы + 1 × Матчи + 1 × ПроцентПобед

При равенстве балла выше тот, кто раньше зарегистрировался на платформе.
Стартовый уровень силы и FAN-очки в балле не участвуют.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import (
    Case,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    OuterRef,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from apps.core.sports import Sport, SportArg, sport_code
from apps.users.models import PlayerSportProfile

if TYPE_CHECKING:
    from django.db.models import QuerySet

# Вес одной победы. Вес матча в 100 раз меньше, поэтому победы остаются
# главным критерием рейтинга, а активность не может их заменить.
WIN_WEIGHT = 100.0
# Вес одного сыгранного матча: активность важна, но не заменяет победы.
MATCH_WEIGHT = 1.0
# Вес одного процента побед. 100% побед добавляют балл одной победы.
WIN_RATE_WEIGHT = 1.0


def ranking_score(wins: int | None, matches: int | None) -> float:
    """Посчитать рейтинговый балл по числу побед и матчей.

    Args:
        wins: Количество побед игрока.
        matches: Количество сыгранных матчей.

    Returns:
        float: Балл рейтинга. У нового игрока без матчей — 0.
    """
    wins = max(0, int(wins or 0))
    matches = max(0, int(matches or 0))
    win_rate = (wins * 100.0 / matches) if matches else 0.0
    return round(
        wins * WIN_WEIGHT + matches * MATCH_WEIGHT + win_rate * WIN_RATE_WEIGHT,
        2,
    )


def annotate_ranking(queryset: QuerySet, sport: SportArg) -> QuerySet:
    """Добавить к queryset игроков поля рейтингового балла.

    Для тенниса берутся счётчики ``Player``, для падела — профиль вида спорта.
    Отсутствующий профиль падела считается нулевой статистикой.

    Args:
        queryset: QuerySet модели ``Player``.
        sport: Код вида спорта.

    Returns:
        QuerySet: С аннотациями ``rank_wins``, ``rank_matches``,
        ``rank_win_rate`` и ``rank_score``.
    """
    if sport_code(sport) == Sport.PADEL:
        profiles = PlayerSportProfile.objects.filter(
            player=OuterRef("pk"),
            sport=Sport.PADEL,
        )
        queryset = queryset.annotate(
            rank_wins=Coalesce(
                Subquery(
                    profiles.values("matches_won")[:1],
                    output_field=IntegerField(),
                ),
                Value(0),
            ),
            rank_matches=Coalesce(
                Subquery(
                    profiles.values("matches_played")[:1],
                    output_field=IntegerField(),
                ),
                Value(0),
            ),
        )
    else:
        queryset = queryset.annotate(
            rank_wins=F("matches_won"),
            rank_matches=F("matches_played"),
        )

    queryset = queryset.annotate(
        rank_win_rate=Case(
            When(rank_matches=0, then=Value(0.0)),
            default=ExpressionWrapper(
                F("rank_wins") * Value(100.0) / F("rank_matches"),
                output_field=FloatField(),
            ),
            output_field=FloatField(),
        )
    )
    return queryset.annotate(
        rank_score=ExpressionWrapper(
            F("rank_wins") * Value(WIN_WEIGHT)
            + F("rank_matches") * Value(MATCH_WEIGHT)
            + F("rank_win_rate") * Value(WIN_RATE_WEIGHT),
            output_field=FloatField(),
        )
    )


def order_by_ranking(queryset: QuerySet, sport: SportArg) -> QuerySet:
    """Отсортировать игроков по рейтинговому баллу.

    Порядок: больше баллов → выше; при равенстве баллов выше тот, кто раньше
    зарегистрировался (время нахождения на платформе). Последний ключ ``pk``
    делает порядок стабильным между страницами пагинации.

    Args:
        queryset: QuerySet модели ``Player``.
        sport: Код вида спорта.

    Returns:
        QuerySet: Отсортированный queryset.
    """
    return annotate_ranking(queryset, sport).order_by(
        "-rank_score",
        "created_at",
        "pk",
    )
