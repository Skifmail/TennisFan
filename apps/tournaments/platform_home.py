"""
Отображение турниров платформы и клубов на главной и в общих списках.

Не содержит бизнес-правил регистрации — только вычисление CTA для UI.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeAlias

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Case, IntegerField, Q, QuerySet, When
from django.urls import reverse

from apps.clubs.models import (
    Club,
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubMember,
    ClubMemberStatus,
    ClubStatus,
)
from apps.tournaments.models import Tournament, TournamentStatus

UserLike: TypeAlias = AbstractBaseUser | AnonymousUser

# Специальные значения query-параметра ``club`` в списках турниров (главная, /tournaments/, таблицы).
CLUB_FILTER_PLATFORM = "__platform__"
CLUB_FILTER_CLUB_ONLY = "__club_only__"

_IN_GAME_STATUSES = (
    TournamentStatus.ACTIVE,
    TournamentStatus.GROUP_STAGE,
    TournamentStatus.PLAYOFFS,
)


def order_tournaments_active_first(
    queryset: QuerySet[Tournament],
) -> QuerySet[Tournament]:
    """Сортировка публичных списков: «в игре» первыми, остальные — по дате создания.

    Сначала турниры в статусах active / group_stage / playoffs, затем все остальные.
    Внутри каждой группы — от недавно созданных к более старым (``-created_at``).

    Args:
        queryset (QuerySet[Tournament]): Исходный queryset турниров.

    Returns:
        QuerySet[Tournament]: Отсортированный queryset.
    """
    return queryset.annotate(
        _list_status_priority=Case(
            When(status__in=_IN_GAME_STATUSES, then=0),
            default=1,
            output_field=IntegerField(),
        )
    ).order_by("_list_status_priority", "-created_at", "-pk")


def club_filter_choices_for_tournament_lists():
    """Возвращает queryset пар (slug, name) для фильтра «Клуб» на главной и /tournaments/.

    Включает клубы в статусе active/trial и клубы с турнирами «идёт набор» / «в игре».

    Returns:
        QuerySet кортежей ``(slug, name)``, отсортированный по названию.
    """
    return (
        Club.objects.filter(
            Q(status__in=(ClubStatus.ACTIVE, ClubStatus.TRIAL))
            | Q(
                tournaments__status__in=(
                    TournamentStatus.UPCOMING,
                    TournamentStatus.ACTIVE,
                ),
            )
        )
        .distinct()
        .order_by("name")
        .values_list("slug", "name")
    )


def load_user_club_ids_for_platform_tournaments(
    user: UserLike,
) -> tuple[set[int], set[int]]:
    """Загружает множества клубов для фильтрации CTA на главной и в карточках.

    Args:
        user: Текущий пользователь (в том числе анонимный).

    Returns:
        Кортеж ``(pending_join_club_ids, active_member_club_ids)`` — id клубов,
        куда отправлена ожидающая заявка, и id клубов, где пользователь активен.
    """
    if not user.is_authenticated:
        return set(), set()
    pending = set(
        ClubJoinRequest.objects.filter(
            user=user,
            status=ClubJoinRequestStatus.PENDING,
        ).values_list("club_id", flat=True)
    )
    members = set(
        ClubMember.objects.filter(
            user=user,
            status=ClubMemberStatus.ACTIVE,
        ).values_list("club_id", flat=True)
    )
    return pending, members


def attach_home_tournament_row_cta(
    tournament: Tournament,
    user: UserLike,
    *,
    pending_join_club_ids: set[int],
    member_club_ids: set[int],
) -> None:
    """Заполняет атрибуты ``home_cta_*`` у турнира для строки таблицы на главной.

    Args:
        tournament: Экземпляр турнира ( queryset ).
        user: Пользователь запроса.
        pending_join_club_ids: Id клубов с ожидающей заявкой на вступление.
        member_club_ids: Id клубов с активным членством пользователя.

    Returns:
        None: поля записываются на объект ``tournament``.
    """
    tournament.home_cta_kind = "login"
    tournament.home_login_next = ""
    tournament.home_join_slug = ""
    tournament.home_join_next = ""
    tournament.home_register_url = ""

    if tournament.status in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED):
        tournament.home_cta_kind = "terminal_status"
        return
    if tournament.bracket_generated:
        tournament.home_cta_kind = "reg_closed"
        return
    is_full = getattr(tournament, "is_full_annotated", None)
    if is_full is None:
        is_full = tournament.is_full()
    if bool(is_full):
        tournament.home_cta_kind = "full"
        return

    if not user.is_authenticated:
        tournament.home_cta_kind = "login"
        tournament.home_login_next = reverse(
            "tournament_detail",
            kwargs={"slug": tournament.slug},
        )
        return

    needs_host_membership = bool(
        tournament.club_id and not tournament.is_open_interclub
    )
    is_host_member = (not tournament.club_id) or (tournament.club_id in member_club_ids)

    if needs_host_membership and not is_host_member:
        tournament.home_join_next = reverse(
            "tournament_detail",
            kwargs={"slug": tournament.slug},
        )
        tournament.home_join_slug = tournament.club.slug if tournament.club else ""
        if tournament.club_id and tournament.club_id in pending_join_club_ids:
            tournament.home_cta_kind = "join_pending"
        else:
            tournament.home_cta_kind = "join_club"
        return

    if tournament.is_doubles():
        tournament.home_register_url = reverse(
            "tournament_register_doubles",
            kwargs={"slug": tournament.slug},
        )
    else:
        tournament.home_register_url = reverse(
            "tournament_register",
            kwargs={"slug": tournament.slug},
        )
    tournament.home_cta_kind = "register"


def attach_home_tournament_rows(
    tournaments: Iterable[Tournament],
    user: UserLike,
    *,
    pending_join_club_ids: set[int],
    member_club_ids: set[int],
) -> None:
    """Применяет :func:`attach_home_tournament_row_cta` к каждому турниру.

    Args:
        tournaments: Список или страница пагинатора турниров.
        user: Пользователь запроса.
        pending_join_club_ids: Ожидающие заявки по клубам.
        member_club_ids: Активные членства.

    Returns:
        None.
    """
    for t in tournaments:
        attach_home_tournament_row_cta(
            t,
            user,
            pending_join_club_ids=pending_join_club_ids,
            member_club_ids=member_club_ids,
        )
