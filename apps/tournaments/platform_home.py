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


def tournament_is_full_for_display(tournament: Tournament) -> bool:
    """Проверить, заполнен ли турнир (с учётом аннотации списка).

    Args:
        tournament (Tournament): Турнир.

    Returns:
        bool: ``True``, если мест/команд больше нет.
    """
    is_full = getattr(tournament, "is_full_annotated", None)
    if is_full is None:
        is_full = tournament.is_full()
    return bool(is_full)


def get_tournament_public_status_label(tournament: Tournament) -> str:
    """Публичная метка статуса набора для карточки и таблицы.

    Не сводится к полю ``status``: учитывает сетку, постоплату и заполненность.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        str: Текст бейджа («Идёт набор», «Мест нет», «В игре» и т.д.).
    """
    if tournament.status == TournamentStatus.CANCELLED:
        return "Отменён"
    if tournament.status == TournamentStatus.COMPLETED:
        return "Завершён"
    if tournament.status in _IN_GAME_STATUSES:
        return "В игре"
    if tournament.bracket_generated or tournament.postpayment_window_started_at:
        return "Регистрация закрыта"
    if tournament_is_full_for_display(tournament):
        return "Мест нет"
    return "Идёт набор"


def _sync_card_action_from_home_cta(tournament: Tournament) -> None:
    """Заполнить ``card_action_*`` для мобильной карточки из ``home_cta_kind``.

    Args:
        tournament (Tournament): Турнир с уже выставленным ``home_cta_kind``.

    Returns:
        None: Атрибуты карточки пишутся на объект.
    """
    tournament.card_status_label = get_tournament_public_status_label(tournament)
    tournament.card_action_label = "—"
    tournament.card_action_url = None
    tournament.card_action_is_primary = False
    tournament.card_action_disabled = True
    tournament.card_action_is_join_form = False
    tournament.card_join_club_slug = ""
    tournament.card_join_next_url = ""
    tournament.card_action_reason = ""

    kind = getattr(tournament, "home_cta_kind", "") or ""
    if kind == "terminal_status":
        if tournament.status == TournamentStatus.CANCELLED:
            tournament.card_action_label = "Турнир отменён"
        elif tournament.status == TournamentStatus.COMPLETED:
            tournament.card_action_label = "Турнир завершён"
        else:
            tournament.card_action_label = "—"
        return
    if kind == "reg_closed":
        tournament.card_action_label = "Регистрация закрыта"
        return
    if kind == "full":
        tournament.card_action_label = "Мест нет"
        return
    if kind == "login":
        tournament.card_action_label = "Войти"
        tournament.card_action_url = (
            f"{reverse('login')}?next={tournament.home_login_next}"
            if tournament.home_login_next
            else reverse("login")
        )
        tournament.card_action_disabled = False
        return
    if kind == "join_pending":
        tournament.card_action_label = "Заявка на вступление отправлена"
        return
    if kind == "join_club":
        tournament.card_action_label = "Вступить в клуб"
        tournament.card_action_is_primary = True
        tournament.card_action_disabled = False
        tournament.card_action_is_join_form = True
        tournament.card_join_club_slug = tournament.home_join_slug
        tournament.card_join_next_url = tournament.home_join_next
        return
    if kind == "register":
        tournament.card_action_label = "Записаться"
        tournament.card_action_url = tournament.home_register_url
        tournament.card_action_is_primary = True
        tournament.card_action_disabled = False
        return
    tournament.card_action_label = "Недоступно"


def attach_home_tournament_row_cta(
    tournament: Tournament,
    user: UserLike,
    *,
    pending_join_club_ids: set[int],
    member_club_ids: set[int],
) -> None:
    """Заполняет атрибуты ``home_cta_*`` и ``card_action_*`` для главной.

    Args:
        tournament: Экземпляр турнира (queryset).
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
        _sync_card_action_from_home_cta(tournament)
        return
    if tournament.bracket_generated or tournament.postpayment_window_started_at:
        tournament.home_cta_kind = "reg_closed"
        _sync_card_action_from_home_cta(tournament)
        return
    if tournament_is_full_for_display(tournament):
        tournament.home_cta_kind = "full"
        _sync_card_action_from_home_cta(tournament)
        return

    if not user.is_authenticated:
        tournament.home_cta_kind = "login"
        tournament.home_login_next = reverse(
            "tournament_detail",
            kwargs={"slug": tournament.slug},
        )
        _sync_card_action_from_home_cta(tournament)
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
        _sync_card_action_from_home_cta(tournament)
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
    _sync_card_action_from_home_cta(tournament)


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
