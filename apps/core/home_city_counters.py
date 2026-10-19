"""Счётчики активных турниров и спаррингов в городе игрока для главной.

Основное число — сколько активных турниров или заявок на спарринг сейчас в
городе профиля. Красный бейдж — сколько из них появилось после последнего
открытия соответствующего списка. Момент просмотра хранится в cookie, поэтому
бейдж работает и для гостей; без cookie новыми считаются записи за последние
:data:`NEW_ITEMS_FALLBACK_DAYS` дней.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any, TypeVar, cast
from urllib.parse import urlencode

from django.db.models import Count, Model, Q, QuerySet
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone

from apps.core.localities import primary_locality_name
from apps.core.text_search import filter_field_contains_ci
from apps.sparring.models import (
    DoublesMatchKind,
    DoublesMatchRequest,
    DoublesMatchRequestStatus,
    SparringMatchType,
    SparringRequest,
)
from apps.tournaments.models import Tournament, TournamentStatus

_ModelT = TypeVar("_ModelT", bound=Model)

logger = logging.getLogger(__name__)

TOURNAMENTS_SEEN_COOKIE = "home_tournaments_seen"
SPARRINGS_SEEN_COOKIE = "home_sparrings_seen"
SEEN_COOKIE_MAX_AGE = 60 * 60 * 24 * 365
NEW_ITEMS_FALLBACK_DAYS = 7

ACTIVE_TOURNAMENT_STATUSES: tuple[str, ...] = cast(
    tuple[str, ...],
    (
        TournamentStatus.UPCOMING,
        TournamentStatus.ACTIVE,
    ),
)
ACTIVE_DOUBLES_STATUSES: tuple[str, ...] = cast(
    tuple[str, ...],
    (
        DoublesMatchRequestStatus.OPEN,
        DoublesMatchRequestStatus.FORMING,
        DoublesMatchRequestStatus.READY,
    ),
)

SPARRING_TYPE_SINGLES = "singles"
SPARRING_TYPE_DOUBLES = "doubles"
SPARRING_TYPE_TEAM = "team"


@dataclass(frozen=True)
class CityCounter:
    """Один счётчик главной: всего активных, сколько из них новых и куда вести.

    Attributes:
        total: Количество активных записей в городе.
        new: Сколько из них появилось после последнего просмотра списка.
        url: Ссылка на список, отфильтрованный по городу.
    """

    total: int
    new: int
    url: str


@dataclass(frozen=True)
class HomeCityCounters:
    """Счётчики турниров и спаррингов для блока метрик на главной.

    Attributes:
        city: Город, по которому считали; пустая строка — по всей платформе.
        tournaments: Счётчик активных турниров.
        sparrings: Счётчик активных заявок на спарринг всех типов.
    """

    city: str
    tournaments: CityCounter
    sparrings: CityCounter


def viewer_city(user: Any) -> str:
    """Город из профиля игрока для фильтра списков.

    Args:
        user: ``request.user``, в том числе анонимный.

    Returns:
        str: Название населённого пункта или пустая строка, если города нет.
    """
    if not getattr(user, "is_authenticated", False):
        return ""
    player = getattr(user, "player", None)
    return primary_locality_name(getattr(player, "city", "") or "")


def viewer_player_id(user: Any) -> int | None:
    """Id игрока текущего пользователя, если профиль есть.

    Args:
        user: ``request.user``, в том числе анонимный.

    Returns:
        int | None: Первичный ключ ``Player`` или ``None``.
    """
    if not getattr(user, "is_authenticated", False):
        return None
    player = getattr(user, "player", None)
    pk = getattr(player, "pk", None)
    return int(pk) if pk else None


def parse_seen_at(raw: str | None) -> datetime | None:
    """Разобрать cookie с моментом последнего просмотра списка.

    Args:
        raw: Unix-время в секундах из cookie или ``None``.

    Returns:
        datetime | None: Момент в UTC либо ``None``, если cookie нет или она битая.
    """
    if not raw:
        return None
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        logger.debug("Некорректная cookie просмотра списка: %r", raw)
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=dt_timezone.utc)
    except (OverflowError, OSError, ValueError):
        logger.debug("Cookie просмотра списка вне диапазона: %r", raw)
        return None


def new_items_threshold(seen_at: datetime | None, *, now: datetime) -> datetime:
    """Граница, после которой запись считается новой.

    Args:
        seen_at: Момент последнего просмотра списка или ``None``.
        now: Текущее время.

    Returns:
        datetime: ``seen_at`` (не позже ``now``) либо ``now`` минус окно по умолчанию.
    """
    if seen_at is None:
        return now - timedelta(days=NEW_ITEMS_FALLBACK_DAYS)
    return min(seen_at, now)


def _filter_city(
    qs: QuerySet[_ModelT], city: str, annotation: str
) -> QuerySet[_ModelT]:
    """Отфильтровать по городу той же логикой, что и страницы списков.

    Args:
        qs: Исходный queryset с полем ``city``.
        city: Город; пустая строка — без фильтра.
        annotation: Уникальное имя аннотации для PostgreSQL.

    Returns:
        QuerySet: Отфильтрованный queryset.
    """
    return filter_field_contains_ci(qs, "city", city, annotation=annotation)


def _list_url(route: str, params: dict[str, str]) -> str:
    """Собрать ссылку на список с непустыми query-параметрами.

    Args:
        route: Имя маршрута Django.
        params: Параметры фильтра.

    Returns:
        str: Относительная ссылка.
    """
    query = urlencode({key: value for key, value in params.items() if value})
    base = cast(str, reverse(route))
    return f"{base}?{query}" if query else base


def _count_tournaments(*, city: str, since: datetime) -> tuple[int, int]:
    """Посчитать активные и новые турниры в городе.

    Args:
        city: Город или пустая строка.
        since: Граница новизны по ``created_at``.

    Returns:
        tuple[int, int]: Всего активных и сколько из них новых.
    """
    qs = _filter_city(
        Tournament.objects.filter(status__in=ACTIVE_TOURNAMENT_STATUSES),
        city,
        "_home_cnt_t_city_l",
    )
    row = qs.aggregate(
        total=Count("pk"),
        new=Count("pk", filter=Q(created_at__gt=since)),
    )
    return int(row["total"] or 0), int(row["new"] or 0)


def _count_sparrings_by_type(
    *, city: str, since: datetime, exclude_player_id: int | None
) -> dict[str, tuple[int, int]]:
    """Посчитать активные и новые заявки на спарринг по вкладкам списка.

    Свои заявки не считаются новыми: о них игрок и так знает.

    Args:
        city: Город или пустая строка.
        since: Граница новизны по ``created_at``.
        exclude_player_id: Автор, чьи заявки не попадают в «новые».

    Returns:
        dict[str, tuple[int, int]]: ``{тип вкладки: (всего, новых)}``.
    """
    singles_new = Q(created_at__gt=since)
    doubles_new = Q(created_at__gt=since)
    if exclude_player_id is not None:
        singles_new &= ~Q(player_id=exclude_player_id)
        doubles_new &= ~Q(created_by_id=exclude_player_id)

    singles = _filter_city(
        SparringRequest.objects.filter(
            status=SparringRequest.Status.ACTIVE,
            match_type=SparringMatchType.SINGLES,
        ),
        city,
        "_home_cnt_s_city_l",
    ).aggregate(total=Count("pk"), new=Count("pk", filter=singles_new))

    classic = Q(kind=DoublesMatchKind.CLASSIC)
    team = Q(kind=DoublesMatchKind.TEAM)
    doubles = _filter_city(
        DoublesMatchRequest.objects.filter(status__in=ACTIVE_DOUBLES_STATUSES),
        city,
        "_home_cnt_d_city_l",
    ).aggregate(
        classic_total=Count("pk", filter=classic),
        classic_new=Count("pk", filter=classic & doubles_new),
        team_total=Count("pk", filter=team),
        team_new=Count("pk", filter=team & doubles_new),
    )
    return {
        SPARRING_TYPE_SINGLES: (
            int(singles["total"] or 0),
            int(singles["new"] or 0),
        ),
        SPARRING_TYPE_DOUBLES: (
            int(doubles["classic_total"] or 0),
            int(doubles["classic_new"] or 0),
        ),
        SPARRING_TYPE_TEAM: (
            int(doubles["team_total"] or 0),
            int(doubles["team_new"] or 0),
        ),
    }


def build_home_city_counters(
    *,
    city: str,
    tournaments_seen_at: datetime | None,
    sparrings_seen_at: datetime | None,
    exclude_player_id: int | None = None,
    now: datetime | None = None,
) -> HomeCityCounters:
    """Собрать счётчики турниров и спаррингов для главной.

    Args:
        city: Город профиля; пустая строка — считать по всей платформе.
        tournaments_seen_at: Когда игрок последний раз открывал список турниров.
        sparrings_seen_at: Когда игрок последний раз открывал список спаррингов.
        exclude_player_id: Игрок, чьи заявки на спарринг не считаются новыми.
        now: Текущее время (для тестов).

    Returns:
        HomeCityCounters: Значения и ссылки для шаблона.
    """
    current = now or timezone.now()
    tournaments_total, tournaments_new = _count_tournaments(
        city=city,
        since=new_items_threshold(tournaments_seen_at, now=current),
    )
    by_type = _count_sparrings_by_type(
        city=city,
        since=new_items_threshold(sparrings_seen_at, now=current),
        exclude_player_id=exclude_player_id,
    )
    return HomeCityCounters(
        city=city,
        tournaments=CityCounter(
            total=tournaments_total,
            new=tournaments_new,
            url=_list_url("tournament_list", {"city": city}),
        ),
        sparrings=CityCounter(
            total=sum(total for total, _ in by_type.values()),
            new=sum(new for _, new in by_type.values()),
            url=reverse("sparring_list"),
        ),
    )


def mark_list_seen(
    response: HttpResponse,
    cookie_name: str,
    *,
    secure: bool,
    now: datetime | None = None,
) -> None:
    """Запомнить, что игрок открыл список, чтобы погасить бейдж на главной.

    Args:
        response: HTTP-ответ страницы списка.
        cookie_name: :data:`TOURNAMENTS_SEEN_COOKIE` или :data:`SPARRINGS_SEEN_COOKIE`.
        secure: Ставить флаг Secure (для HTTPS).
        now: Текущее время (для тестов).
    """
    moment = now or timezone.now()
    response.set_cookie(
        cookie_name,
        str(int(moment.timestamp())),
        max_age=SEEN_COOKIE_MAX_AGE,
        path="/",
        samesite="Lax",
        secure=secure,
        httponly=True,
    )
