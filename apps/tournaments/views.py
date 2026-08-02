"""
Tournaments views.
"""

import json
import logging
from collections import defaultdict
from functools import wraps
from itertools import groupby
from typing import cast
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Count, Min, Prefetch, Q
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.clubs.models import (
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubMember,
    ClubMembershipFee,
    ClubMemberStatus,
)
from apps.clubs.plan_services import (
    RegistrationMode,
    can_member_register_for_tournament,
    consume_member_tournament_limit,
)
from apps.clubs.plan_services import (
    check_tournament_registration_eligibility as check_club_tournament_registration_eligibility,
)
from apps.clubs.services import get_fee_status_for_member, user_can_manage_club
from apps.core.decorators import require_filled_profile, require_verified_player
from apps.core.text_search import filter_field_contains_ci
from apps.subscriptions.fancoin import TOURNAMENT_REGISTRATION_COST
from apps.subscriptions.models import FancoinTransaction
from apps.users.models import Notification, Player, SkillLevel

from .cancel import cancel_tournament
from .fan import _is_fan
from .fan import generate_bracket as fan_generate_bracket
from .models import (
    Match,
    MatchResultProposal,
    Tournament,
    TournamentEntryPayment,
    TournamentEntryRefundRequest,
    TournamentPlayerResult,
    TournamentPostpaymentInvoice,
    TournamentRegistrationCoverage,
    TournamentStatus,
    TournamentTeam,
    TournamentType,
)
from .olympic_consolation import _is_olympic
from .olympic_consolation import generate_bracket as olympic_generate_bracket
from .platform_home import (
    CLUB_FILTER_CLUB_ONLY,
    CLUB_FILTER_PLATFORM,
    club_filter_choices_for_tournament_lists,
    order_tournaments_active_first,
)
from .postpayment import (
    finalize_postpayment_window,
    format_postpayment_open_summary,
    get_pending_postpayment_users,
    get_postpayment_progress,
    mark_registration_covered,
    open_postpayment_window,
    tournament_allows_postpayment_registration,
)
from .proposal_service import (
    ProposalValidationError,
    apply_proposal,
    derive_proposer_result_from_score,
)
from .round_robin import (
    _is_round_robin,
    compute_standings,
    compute_standings_for_entities,
    get_match_matrix,
    get_match_matrix_for_entities,
)
from .round_robin import (
    generate_bracket as round_robin_generate_bracket,
)
from .tables_stats import build_tables_dashboard
from .tvd import (
    TVD_STAGE_CONSOLATION_RR,
    TVD_STAGE_GROUP,
    TVD_STAGE_MAIN_RR,
    TVD_STAGE_MAIN_RR_1_3,
    TVD_STAGE_MAIN_RR_4_6,
    _is_tvd,
    get_tvd_rr_entities_and_matches,
)
from .tvd import (
    check_and_finalize as tvd_check_and_finalize,
)
from .tvd import (
    generate_consolation_bracket as tvd_generate_consolation_bracket,
)
from .tvd import (
    generate_groups as tvd_generate_groups,
)
from .tvd import (
    generate_main_bracket as tvd_generate_main_bracket,
)
from .tvd import (
    generate_playoffs as tvd_generate_playoffs,
)
from .tvd import (
    is_group_stage_complete as tvd_is_group_stage_complete,
)
from .utils import (
    attach_tournament_result_order_flags,
    find_blocking_earlier_tournament_match,
    format_tournament_match_order_block_message,
    get_match_opponent_users,
    order_player_matches_for_display,
)

logger = logging.getLogger(__name__)

MATCH_FORMAT_DESCRIPTIONS = {
    "1_set_6": "1 сет до 6 геймов. Матч до 6 выигранных геймов (при счёте 6:6 — игра до 7).",
    "1_set_tiebreak": "1 сет с тай-брейком. Матч до 6 геймов, при 6:6 — тай-брейк до 7 очков.",
    "2_sets": (
        "2 сета до победы. Побеждает тот, кто выиграет 2 сета. "
        "При счёте 1:1 — третий сет (по договорённости игроков вместо сета можно сыграть тай-брейк)."
    ),
    "fast4": "2 коротких сета + супертай-брейк. Сеты до 4 геймов, при 1:1 — супертай-брейк до 10 очков.",
}


def _get_safe_next_url(request, fallback: str) -> str:
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback


def login_required_with_message(
    message="Данная информация доступна только для зарегистрированных пользователей.",
):
    """
    Декоратор, требующий авторизации и показывающий сообщение при редиректе.
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                messages.info(request, message)
                from django.conf import settings

                login_url = getattr(settings, "LOGIN_URL", "login")

                next_url = request.get_full_path()
                return redirect(f"{reverse(login_url)}?next={next_url}")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def _get_club_panel_context_for_tournament(request, tournament):
    """Return club panel context for active club members viewing a club tournament."""
    if (
        tournament is None
        or not tournament.club_id
        or not request.user.is_authenticated
    ):
        return {}

    club = tournament.club
    is_member = ClubMember.objects.filter(
        club=club,
        user=request.user,
        status=ClubMemberStatus.ACTIVE,
    ).exists()
    if not is_member:
        return {}

    return {"is_club_panel": True, "club": club}


def _match_participants(match):
    """
    Возвращает множество игроков — участников матча.
    Для одиночных: player1, player2. Для парных: все четверо из team1 и team2.
    """
    participants = set()
    if match.team1 and match.team2:
        for team in (match.team1, match.team2):
            if team.player1_id:
                participants.add(team.player1)
            if team.player2_id:
                participants.add(team.player2)
    else:
        if match.player1_id:
            participants.add(match.player1)
        if match.player2_id:
            participants.add(match.player2)
    return participants


def _build_bracket_standings(tournament, is_fan, is_olympic):
    """
    Построить турнирную таблицу для страницы турнира (FAN или Олимпийская система).
    Возвращает список словарей с ключами place, player, team, fan_points, round_eliminated или None.
    """
    if not (is_fan or is_olympic):
        return None
    if is_olympic:
        results_by_place = list(
            tournament.fan_results.filter(place__isnull=False)
            .select_related("player__user")
            .order_by("place")
        )
        if not results_by_place:
            return None
        if tournament.is_doubles():
            seen_places = set()
            standings = []
            for r in results_by_place:
                if r.place in seen_places:
                    continue
                seen_places.add(r.place)
                team = (
                    tournament.teams.filter(
                        Q(player1=r.player) | Q(player2=r.player), player2__isnull=False
                    )
                    .select_related("player1__user", "player2__user")
                    .first()
                )
                standings.append(
                    {
                        "place": r.place,
                        "player": team.player1 if team else r.player,
                        "team": team,
                        "fan_points": r.fan_points,
                        "round_eliminated": f"Место {r.place}",
                    }
                )
        else:
            standings = [
                {
                    "place": r.place,
                    "player": r.player,
                    "team": None,
                    "fan_points": r.fan_points,
                    "round_eliminated": f"Место {r.place}",
                }
                for r in results_by_place
            ]
        return standings
    # FAN
    fan_results = {}
    for r in tournament.fan_results.select_related("player__user"):
        fan_results[r.player_id] = r
    if tournament.is_doubles():
        teams = list(
            tournament.teams.filter(player2__isnull=False).select_related(
                "player1__user", "player2__user"
            )
        )
        teams_sorted = sorted(
            teams,
            key=lambda t: (
                -(
                    fan_results.get(t.player1_id).fan_points
                    if fan_results.get(t.player1_id)
                    else 0
                ),
                -t.player1.total_points,
            ),
        )
        standings = []
        for i, team in enumerate(teams_sorted, 1):
            fr = fan_results.get(team.player1_id)
            standings.append(
                {
                    "place": i,
                    "player": team.player1,
                    "team": team,
                    "fan_points": fr.fan_points if fr else 0,
                    "round_eliminated": (
                        fr.get_round_eliminated_display() if fr else "—"
                    ),
                }
            )
    else:
        participants = list(
            tournament.participants.select_related("user").order_by("-total_points")
        )
        participants_sorted = sorted(
            participants,
            key=lambda p: (
                -(fan_results.get(p.id).fan_points if fan_results.get(p.id) else 0),
                -p.total_points,
            ),
        )
        standings = []
        for i, p in enumerate(participants_sorted, 1):
            fr = fan_results.get(p.id)
            standings.append(
                {
                    "place": i,
                    "player": p,
                    "team": None,
                    "fan_points": fr.fan_points if fr else 0,
                    "round_eliminated": (
                        fr.get_round_eliminated_display() if fr else "—"
                    ),
                }
            )
    return standings


def tournament_list(request, *, archive: bool = False):
    """Список турниров платформы и клубов.

    Завершённые турниры по умолчанию скрыты и доступны через фильтр
    «Завершённые» или отдельную страницу архива.

    Args:
        request (HttpRequest): HTTP-запрос со query-параметрами фильтров.
        archive (bool): Если True — показывать только завершённые турниры.

    Returns:
        HttpResponse: Страница списка или архива турниров.
    """
    city = (request.GET.get("city") or "").strip()
    category = request.GET.get("category", "")
    if archive:
        status = TournamentStatus.COMPLETED
    else:
        status = request.GET.get("status", "")
    club_filter = (request.GET.get("club") or "").strip()

    # Турниры платформы и клубов (клубные — с отдельным CTA «Вступить в клуб»).
    tournaments = (
        Tournament.objects.all()
        .annotate(
            participants_count=Count("participants", distinct=True),
            teams_count=Count("teams", distinct=True),
            full_teams_count_annotated=Count(
                "teams",
                filter=Q(teams__player2__isnull=False),
                distinct=True,
            ),
        )
        .select_related("court", "club")
        .prefetch_related(
            "participants__user",
            "allowed_categories",
            "teams__player1__user",
            "teams__player2__user",
        )
    )

    if city:
        tournaments = filter_field_contains_ci(
            tournaments, "city", city, annotation="_tlist_city_l"
        )
    if category:
        tournaments = tournaments.filter(
            allowed_categories__category=category
        ).distinct()
    if status:
        tournaments = tournaments.filter(status=status)
    else:
        # В общем списке завершённые не показываем — только по явному фильтру/архиву.
        tournaments = tournaments.exclude(status=TournamentStatus.COMPLETED)
    if club_filter == CLUB_FILTER_PLATFORM:
        tournaments = tournaments.filter(club__isnull=True)
    elif club_filter == CLUB_FILTER_CLUB_ONLY:
        tournaments = tournaments.filter(club__isnull=False)
    elif club_filter:
        tournaments = tournaments.filter(club__slug=club_filter)

    tournaments = order_tournaments_active_first(tournaments)
    paginator = Paginator(tournaments, 20)
    page_number = request.GET.get("page")
    tournaments_page = paginator.get_page(page_number)
    tournaments_page_list = list(tournaments_page.object_list)

    current_player = None
    pending_join_club_ids: set[int] = set()
    member_club_ids: set[int] = set()
    if request.user.is_authenticated:
        current_player = getattr(request.user, "player", None)
        if current_player is None:
            current_player = Player.objects.filter(user=request.user).first()
        pending_join_club_ids = set(
            ClubJoinRequest.objects.filter(
                user=request.user,
                status=ClubJoinRequestStatus.PENDING,
            ).values_list("club_id", flat=True)
        )
        member_club_ids = set(
            ClubMember.objects.filter(
                user=request.user,
                status=ClubMemberStatus.ACTIVE,
            ).values_list("club_id", flat=True)
        )

    from apps.tournaments.platform_home import get_tournament_public_status_label

    for tournament in tournaments_page_list:
        tournament.card_action_label = "Записаться"
        tournament.card_action_url = None
        tournament.card_action_is_primary = False
        tournament.card_action_disabled = True
        tournament.card_action_is_join_form = False
        tournament.card_join_club_slug = ""
        tournament.card_join_next_url = ""
        if tournament.is_doubles():
            current_slots_count = int(
                getattr(tournament, "full_teams_count_annotated", 0)
            )
            max_slots = tournament.max_teams
        else:
            current_slots_count = int(getattr(tournament, "participants_count", 0))
            max_slots = tournament.max_participants
        tournament.current_slots_count = current_slots_count
        tournament.is_full_annotated = bool(
            max_slots and current_slots_count >= max_slots
        )
        tournament.card_status_label = get_tournament_public_status_label(tournament)

        if tournament.status == TournamentStatus.COMPLETED:
            tournament.card_action_label = "Турнир завершён"
            continue
        if tournament.status == TournamentStatus.CANCELLED:
            tournament.card_action_label = "Турнир отменён"
            continue
        if tournament.status != TournamentStatus.UPCOMING:
            tournament.card_action_label = "Регистрация закрыта"
            continue
        if tournament.bracket_generated or tournament.postpayment_window_started_at:
            tournament.card_action_label = "Регистрация закрыта"
            continue
        if tournament.is_full_annotated:
            tournament.card_action_label = "Мест нет"
            continue

        if not request.user.is_authenticated:
            tournament.card_action_label = "Войти"
            tournament.card_action_url = (
                f"{reverse('login')}?next={reverse('tournament_list')}"
            )
            tournament.card_action_is_primary = False
            tournament.card_action_disabled = False
            continue

        if current_player is None:
            tournament.card_action_label = "Недоступно"
            continue

        if tournament.is_doubles():
            user_is_registered = _is_player_registered_in_doubles(
                tournament, current_player
            )
            register_url = reverse(
                "tournament_register_doubles",
                kwargs={"slug": tournament.slug},
            )
        else:
            user_is_registered = tournament.participants.filter(
                id=current_player.id
            ).exists()
            register_url = reverse(
                "tournament_register",
                kwargs={"slug": tournament.slug},
            )

        if user_is_registered:
            tournament.card_action_label = "Вы записаны"
            continue

        if (
            tournament.club_id
            and not tournament.is_open_interclub
            and tournament.club_id not in member_club_ids
        ):
            tournament.card_join_next_url = reverse(
                "tournament_detail",
                kwargs={"slug": tournament.slug},
            )
            tournament.card_join_club_slug = tournament.club.slug
            if tournament.club_id in pending_join_club_ids:
                tournament.card_action_label = "Заявка на вступление отправлена"
                tournament.card_action_disabled = True
            else:
                tournament.card_action_label = "Вступить в клуб"
                tournament.card_action_is_primary = True
                tournament.card_action_disabled = False
                tournament.card_action_is_join_form = True
            continue

        can_register, club_plan_error = True, ""
        tournament_member = _get_tournament_club_member(request.user, tournament)
        if tournament_member:
            can_register, club_plan_error = can_member_register_for_tournament(
                tournament_member,
                tournament,
            )
        elif current_player:
            can_register, error_message = _check_tournament_registration_eligibility(
                request,
                tournament,
                current_player,
            )
            club_plan_error = error_message or ""

        if not can_register:
            tournament.card_action_label = "Недоступно"
            tournament.card_action_reason = club_plan_error
            continue

        tournament.card_action_label = "Записаться"
        tournament.card_action_url = register_url
        tournament.card_action_is_primary = True
        tournament.card_action_disabled = False

    context = {
        "tournaments": tournaments_page_list,
        "tournaments_page": tournaments_page,
        "current_city": city,
        "current_category": category,
        "current_status": status,
        # Не использовать ключ current_club — он зарезервирован под клуб из context processor (base.html).
        "list_club_filter": club_filter,
        "club_filter_choices": club_filter_choices_for_tournament_lists(),
        "category_choices": SkillLevel.choices,
        "is_archive": archive,
    }
    return render(request, "tournaments/list.html", context)


def tournament_archive(request):
    """Архив завершённых турниров.

    Args:
        request (HttpRequest): HTTP-запрос со query-параметрами фильтров.

    Returns:
        HttpResponse: Страница архива завершённых турниров.
    """
    return tournament_list(request, archive=True)


def _get_interclub_context(request, tournament):
    """Контекст межклубных заявок для страницы турнира."""
    if not tournament.is_open_interclub or not tournament.club_id:
        return {}

    from apps.clubs.models import (
        ClubMemberRole,
        ClubMemberStatus,
        ClubTournamentApplication,
    )

    ctx: dict = {"is_interclub": True}

    if not request.user.is_authenticated:
        return ctx

    admin_clubs = list(
        request.user.club_memberships.filter(
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )
        .exclude(club_id=tournament.club_id)
        .select_related("club")
    )
    if not admin_clubs:
        return ctx

    existing_apps = {
        a.applicant_club_id: a
        for a in ClubTournamentApplication.objects.filter(
            tournament=tournament,
            applicant_club_id__in=[m.club_id for m in admin_clubs],
        )
    }

    can_apply_clubs = []
    applied_clubs = []
    for m in admin_clubs:
        app = existing_apps.get(m.club_id)
        if app:
            applied_clubs.append({"club": m.club, "application": app})
        else:
            can_apply_clubs.append(m.club)

    ctx["interclub_can_apply_clubs"] = can_apply_clubs
    ctx["interclub_applied_clubs"] = applied_clubs
    return ctx


def tournament_detail(request, slug):
    """Tournament detail page.

    Внутриклубные турниры (клуб задан, не межклубные) доступны для просмотра всем;
    зарегистрироваться может только активный член клуба-организатора.
    """
    tournament = get_object_or_404(
        Tournament.objects.select_related("court", "club").prefetch_related(
            "matches__player1__user",
            "matches__player2__user",
            "matches__winner__user",
            "matches__team1__player1__user",
            "matches__team1__player2__user",
            "matches__team2__player1__user",
            "matches__team2__player2__user",
            "participants__user",
            "fan_results__player__user",
            "teams__player1__user",
            "teams__player2__user",
            "allowed_categories",
            "tvd_groups__members__player__user",
            "tvd_groups__matches__player1__user",
            "tvd_groups__matches__player2__user",
            "tvd_groups__matches__winner__user",
            "photos",
        ),
        slug=slug,
    )

    # Внутриклубные турниры доступны для просмотра всем; регистрация — только членам
    # клуба (см. can_register и блок «Вступить в клуб» в шаблоне).
    is_fan = _is_fan(tournament)
    is_olympic = _is_olympic(tournament)
    is_round_robin = _is_round_robin(tournament)
    is_tvd = _is_tvd(tournament)

    if is_fan or is_olympic:
        matches = tournament.matches.order_by(
            "is_consolation", "round_index", "round_order"
        )

        def round_key(m):
            return (m.round_name, m.is_consolation)

        matches_by_round = []
        for k, group in groupby(matches, key=round_key):
            matches_by_round.append((k[0], k[1], list(group)))
        matrix_participants = None
        matrix_data = None
        matrix_rows = None
        rr_standings = None
        tvd_groups = None
        tvd_main_matches = None
        tvd_consolation_matches = None
        tvd_main_rounds = None
        tvd_consolation_rounds = None
        tvd_standings = None
        standings = _build_bracket_standings(tournament, is_fan, is_olympic)
    elif is_round_robin:
        matches = tournament.matches.filter(is_consolation=False).order_by(
            "round_index", "round_order"
        )
        matches_by_round = []
        for _, g in groupby(matches, key=lambda m: m.round_index):
            round_matches = list(g)
            if round_matches:
                matches_by_round.append(
                    (round_matches[0].round_name, False, round_matches)
                )
        matrix_participants, matrix_data = get_match_matrix(tournament)
        rr_standings = compute_standings(tournament)
        if tournament.status == "completed":
            fan_results_map = {
                r.player_id: r.fan_points
                for r in tournament.fan_results.select_related("player")
            }
            for row in rr_standings:
                pid = row["team"].player1_id if row.get("team") else row["player"].id
                row["rating_points"] = fan_results_map.get(pid)
        else:
            for row in rr_standings:
                row["rating_points"] = None

        def entity_id(r):
            return (r["team"] or r["player"]).id

        standings_by_entity = {entity_id(row): row for row in rr_standings}
        matrix_rows = []
        for i, p in enumerate(matrix_participants):
            row_cells = matrix_data[i] if i < len(matrix_data) else []
            st = standings_by_entity.get(p.id, {})
            matrix_rows.append(
                {
                    "participant": p,
                    "cells": row_cells,
                    "place": st.get("place"),
                    "points": st.get("points"),
                }
            )
        standings = None
        tvd_groups = None
        tvd_main_matches = None
        tvd_consolation_matches = None
        tvd_main_rounds = None
        tvd_consolation_rounds = None
        tvd_standings = None
    elif is_tvd:
        matches = tournament.matches.order_by(
            "is_consolation", "round_index", "round_order"
        )
        matches_by_round = []
        for m in matches:
            key = (m.round_name, m.is_consolation)
            found = next((r for r in matches_by_round if (r[0], r[1]) == key), None)
            if found:
                found[2].append(m)
            else:
                matches_by_round.append((m.round_name, m.is_consolation, [m]))
        matrix_participants = None
        matrix_data = None
        matrix_rows = None
        rr_standings = None
        standings = None
        tvd_groups = list(
            tournament.tvd_groups.prefetch_related(
                "members__player__user",
                "members__team__player1__user",
                "members__team__player2__user",
                "matches__player1__user",
                "matches__player2__user",
                "matches__winner__user",
                "matches__team1__player1__user",
                "matches__team1__player2__user",
                "matches__team2__player1__user",
                "matches__team2__player2__user",
                "matches__winner_team__player1__user",
                "matches__winner_team__player2__user",
            ).order_by("order")
        )
        tvd_main_matches = list(
            tournament.matches.filter(
                is_consolation=False,
                tvd_stage__in=(
                    "main_qf",
                    "main_sf",
                    "main_final",
                    "third_place",
                    "main_round_robin",
                    "main_rr_1_3",
                    "main_rr_4_6",
                ),
            ).order_by("round_index", "round_order")
        )
        tvd_consolation_matches = list(
            tournament.matches.filter(
                is_consolation=True,
                tvd_stage__in=(
                    "consolation_sf",
                    "consolation_final",
                    "consolation_round_robin",
                ),
            ).order_by("round_index", "round_order")
        )
        tvd_main_rounds = _group_matches_by_round(tvd_main_matches)
        tvd_consolation_rounds = _group_matches_by_round(tvd_consolation_matches)
        tvd_standings = list(
            tournament.fan_results.filter(place__isnull=False)
            .select_related("player__user")
            .order_by("place")
        )
        tvd_main_rr_standings = None
        tvd_main_rr_matrix_rows = None
        tvd_main_rr_matrix_participants = None
        tvd_cons_rr_standings = None
        tvd_cons_rr_matrix_rows = None
        tvd_cons_rr_matrix_participants = None
        tvd_rr_1_3_standings = None
        tvd_rr_1_3_matrix_rows = None
        tvd_rr_1_3_matrix_participants = None
        tvd_rr_4_6_standings = None
        tvd_rr_4_6_matrix_rows = None
        tvd_rr_4_6_matrix_participants = None
        main_rr_entities, main_rr_matches = get_tvd_rr_entities_and_matches(
            tournament, TVD_STAGE_MAIN_RR
        )
        if main_rr_entities and main_rr_matches:
            tvd_main_rr_standings = compute_standings_for_entities(
                tournament, main_rr_entities, main_rr_matches
            )
            tvd_main_rr_matrix_participants, main_rr_matrix = (
                get_match_matrix_for_entities(
                    tournament, main_rr_entities, main_rr_matches
                )
            )
            standings_by_entity = {
                (row["team"] or row["player"]).id: row for row in tvd_main_rr_standings
            }
            tvd_main_rr_matrix_rows = []
            for i, p in enumerate(tvd_main_rr_matrix_participants):
                row_cells = main_rr_matrix[i] if i < len(main_rr_matrix) else []
                st = standings_by_entity.get(p.id, {})
                tvd_main_rr_matrix_rows.append(
                    {
                        "participant": p,
                        "cells": row_cells,
                        "place": st.get("place"),
                        "points": st.get("points"),
                    }
                )
        cons_rr_entities, cons_rr_matches = get_tvd_rr_entities_and_matches(
            tournament, TVD_STAGE_CONSOLATION_RR
        )
        if cons_rr_entities and cons_rr_matches:
            tvd_cons_rr_standings = compute_standings_for_entities(
                tournament, cons_rr_entities, cons_rr_matches
            )
            tvd_cons_rr_matrix_participants, cons_rr_matrix = (
                get_match_matrix_for_entities(
                    tournament, cons_rr_entities, cons_rr_matches
                )
            )
            standings_by_entity = {
                (row["team"] or row["player"]).id: row for row in tvd_cons_rr_standings
            }
            tvd_cons_rr_matrix_rows = []
            for i, p in enumerate(tvd_cons_rr_matrix_participants):
                row_cells = cons_rr_matrix[i] if i < len(cons_rr_matrix) else []
                st = standings_by_entity.get(p.id, {})
                tvd_cons_rr_matrix_rows.append(
                    {
                        "participant": p,
                        "cells": row_cells,
                        "place": st.get("place"),
                        "points": st.get("points"),
                    }
                )
        tvd_rr_1_3_standings = None
        tvd_rr_1_3_matrix_rows = None
        tvd_rr_1_3_matrix_participants = None
        tvd_rr_4_6_standings = None
        tvd_rr_4_6_matrix_rows = None
        tvd_rr_4_6_matrix_participants = None
        rr_1_3_entities, rr_1_3_matches = get_tvd_rr_entities_and_matches(
            tournament, TVD_STAGE_MAIN_RR_1_3
        )
        if rr_1_3_entities and rr_1_3_matches:
            tvd_rr_1_3_standings = compute_standings_for_entities(
                tournament, rr_1_3_entities, rr_1_3_matches
            )
            tvd_rr_1_3_matrix_participants, rr_1_3_matrix = (
                get_match_matrix_for_entities(
                    tournament, rr_1_3_entities, rr_1_3_matches
                )
            )
            st_by_entity = {
                (r["team"] or r["player"]).id: r for r in tvd_rr_1_3_standings
            }
            tvd_rr_1_3_matrix_rows = []
            for i, p in enumerate(tvd_rr_1_3_matrix_participants):
                cells = rr_1_3_matrix[i] if i < len(rr_1_3_matrix) else []
                st = st_by_entity.get(p.id, {})
                tvd_rr_1_3_matrix_rows.append(
                    {
                        "participant": p,
                        "cells": cells,
                        "place": st.get("place"),
                        "points": st.get("points"),
                    }
                )
        rr_4_6_entities, rr_4_6_matches = get_tvd_rr_entities_and_matches(
            tournament, TVD_STAGE_MAIN_RR_4_6
        )
        if rr_4_6_entities and rr_4_6_matches:
            tvd_rr_4_6_standings = compute_standings_for_entities(
                tournament, rr_4_6_entities, rr_4_6_matches
            )
            tvd_rr_4_6_matrix_participants, rr_4_6_matrix = (
                get_match_matrix_for_entities(
                    tournament, rr_4_6_entities, rr_4_6_matches
                )
            )
            st_by_entity = {
                (r["team"] or r["player"]).id: r for r in tvd_rr_4_6_standings
            }
            tvd_rr_4_6_matrix_rows = []
            for i, p in enumerate(tvd_rr_4_6_matrix_participants):
                cells = rr_4_6_matrix[i] if i < len(rr_4_6_matrix) else []
                st = st_by_entity.get(p.id, {})
                tvd_rr_4_6_matrix_rows.append(
                    {
                        "participant": p,
                        "cells": cells,
                        "place": st.get("place"),
                        "points": st.get("points"),
                    }
                )
    else:
        matches_by_round = None
        matches = tournament.matches.all().order_by("-scheduled_datetime")
        matrix_participants = None
        matrix_data = None
        matrix_rows = None
        rr_standings = None
        standings = None
        tvd_groups = None
        tvd_main_matches = None
        tvd_consolation_matches = None
        tvd_main_rounds = None
        tvd_consolation_rounds = None
        tvd_standings = None
        tvd_main_rr_standings = None
        tvd_main_rr_matrix_rows = None
        tvd_main_rr_matrix_participants = None
        tvd_cons_rr_standings = None
        tvd_cons_rr_matrix_rows = None
        tvd_cons_rr_matrix_participants = None
        tvd_rr_1_3_standings = None
        tvd_rr_1_3_matrix_rows = None
        tvd_rr_1_3_matrix_participants = None
        tvd_rr_4_6_standings = None
        tvd_rr_4_6_matrix_rows = None
        tvd_rr_4_6_matrix_participants = None

    if tournament.is_doubles():
        _ensure_doubles_participants_have_teams(tournament)
        _remove_duplicate_doubles_teams(tournament)
        _remove_solo_teams_for_teamed_players(tournament)
        participants_qs = []
        for team in tournament.teams.select_related(
            "player1__user", "player2__user"
        ).order_by("created_at"):
            participants_qs.append(team)
        solo_teams = [t for t in participants_qs if not t.player2_id]

        # Для микст-турниров фильтруем команды по противоположному полу
        if tournament.is_mixed_doubles() and request.user.is_authenticated:
            current_player = getattr(request.user, "player", None)
            if current_player and current_player.gender:
                solo_teams = [
                    t
                    for t in solo_teams
                    if t.player1.gender and t.player1.gender != current_player.gender
                ]

        can_join_team = (
            request.user.is_authenticated
            and getattr(request.user, "player", None)
            and not _is_player_registered_in_doubles(tournament, request.user.player)
            and solo_teams
        )
    else:
        participants_qs = tournament.participants.all()
        solo_teams = []
        can_join_team = False
        if is_fan or is_olympic or is_tvd:
            participants_qs = participants_qs.order_by("-total_points")
        else:
            participants_qs = participants_qs.order_by(
                "user__last_name", "user__first_name"
            )

    match_format_description = None
    if is_round_robin and tournament.match_format:
        match_format_description = MATCH_FORMAT_DESCRIPTIONS.get(
            tournament.match_format, tournament.get_match_format_display()
        )

    # Проверяем, может ли пользователь зарегистрироваться
    user_is_registered = False
    can_register = False
    club_plan_error = ""
    registration_closed = bool(
        tournament.bracket_generated
        or tournament.is_full()
        or tournament.postpayment_window_started_at
    )

    if request.user.is_authenticated:
        current_player = getattr(request.user, "player", None)
        if current_player:
            if tournament.is_doubles():
                user_is_registered = _is_player_registered_in_doubles(
                    tournament, current_player
                )
            else:
                user_is_registered = tournament.participants.filter(
                    id=current_player.id
                ).exists()

            # Может зарегистрироваться, если не зарегистрирован и регистрация открыта
            can_register = not user_is_registered and not registration_closed
            if can_register:
                tournament_member = _get_tournament_club_member(
                    request.user, tournament
                )
                if tournament.club_id and not tournament.is_open_interclub:
                    if not tournament_member:
                        can_register = False
                elif tournament_member:
                    can_register_by_plan, plan_error = (
                        can_member_register_for_tournament(
                            tournament_member,
                            tournament,
                        )
                    )
                    if not can_register_by_plan:
                        can_register = False
                        club_plan_error = plan_error

    show_club_join_cta = False
    show_club_join_pending = False
    if (
        request.user.is_authenticated
        and tournament.club_id
        and not tournament.is_open_interclub
        and tournament.status
        not in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED)
    ):
        is_host_member = ClubMember.objects.filter(
            club_id=tournament.club_id,
            user=request.user,
            status=ClubMemberStatus.ACTIVE,
        ).exists()
        if not is_host_member and not user_is_registered:
            show_club_join_pending = ClubJoinRequest.objects.filter(
                club_id=tournament.club_id,
                user=request.user,
                status=ClubJoinRequestStatus.PENDING,
            ).exists()
            show_club_join_cta = not show_club_join_pending

    interclub_ctx = _get_interclub_context(request, tournament)

    from .withdraw import get_withdrawn_player_ids, get_withdrawn_team_ids

    withdrawn_player_ids = get_withdrawn_player_ids(tournament)
    withdrawn_team_ids = get_withdrawn_team_ids(tournament)

    context = {
        "tournament": tournament,
        "allowed_categories_sorted": sorted(
            list(tournament.allowed_categories.all()),
            key=lambda ac: (
                [
                    SkillLevel.NOVICE,
                    SkillLevel.AMATEUR,
                    SkillLevel.EXPERIENCED,
                    SkillLevel.ADVANCED,
                    SkillLevel.PROFESSIONAL,
                ].index(ac.category)
                if ac.category
                in [
                    SkillLevel.NOVICE,
                    SkillLevel.AMATEUR,
                    SkillLevel.EXPERIENCED,
                    SkillLevel.ADVANCED,
                    SkillLevel.PROFESSIONAL,
                ]
                else 999
            ),
        ),
        "matches": matches,
        "matches_by_round": matches_by_round,
        "matrix_participants": matrix_participants,
        "matrix_data": matrix_data,
        "matrix_rows": matrix_rows,
        "rr_standings": rr_standings,
        "standings": standings,
        "is_fan": is_fan,
        "is_olympic": is_olympic,
        "is_round_robin": is_round_robin,
        "is_tvd": is_tvd,
        "match_format_description": match_format_description,
        "participants": participants_qs,
        "solo_teams": solo_teams,
        "can_join_team": can_join_team,
        "user_is_registered": user_is_registered,
        "can_register": can_register,
        "club_plan_error": club_plan_error,
        "registration_closed": registration_closed,
        "show_club_join_cta": show_club_join_cta,
        "show_club_join_pending": show_club_join_pending,
        "tournament_join_next": request.get_full_path(),
        **interclub_ctx,
        "tvd_groups": tvd_groups if is_tvd else None,
        "tvd_main_matches": tvd_main_matches if is_tvd else None,
        "tvd_consolation_matches": tvd_consolation_matches if is_tvd else None,
        "tvd_main_rounds": tvd_main_rounds if is_tvd else None,
        "tvd_consolation_rounds": tvd_consolation_rounds if is_tvd else None,
        "tvd_standings": tvd_standings if is_tvd else None,
        "tvd_main_rr_standings": tvd_main_rr_standings if is_tvd else None,
        "tvd_main_rr_matrix_rows": tvd_main_rr_matrix_rows if is_tvd else None,
        "tvd_main_rr_matrix_participants": (
            tvd_main_rr_matrix_participants if is_tvd else None
        ),
        "tvd_cons_rr_standings": tvd_cons_rr_standings if is_tvd else None,
        "tvd_cons_rr_matrix_rows": tvd_cons_rr_matrix_rows if is_tvd else None,
        "tvd_cons_rr_matrix_participants": (
            tvd_cons_rr_matrix_participants if is_tvd else None
        ),
        "tvd_rr_1_3_standings": tvd_rr_1_3_standings if is_tvd else None,
        "tvd_rr_1_3_matrix_rows": tvd_rr_1_3_matrix_rows if is_tvd else None,
        "tvd_rr_1_3_matrix_participants": (
            tvd_rr_1_3_matrix_participants if is_tvd else None
        ),
        "tvd_rr_4_6_standings": tvd_rr_4_6_standings if is_tvd else None,
        "tvd_rr_4_6_matrix_rows": tvd_rr_4_6_matrix_rows if is_tvd else None,
        "tvd_rr_4_6_matrix_participants": (
            tvd_rr_4_6_matrix_participants if is_tvd else None
        ),
        "can_manage_tournament": _can_manage_tournament(request, tournament),
        "withdrawn_player_ids": withdrawn_player_ids,
        "withdrawn_team_ids": withdrawn_team_ids,
    }
    context.update(_get_club_panel_context_for_tournament(request, tournament))
    if is_tvd:
        return render(request, "tournaments/tvd_detail.html", context)
    return render(request, "tournaments/detail.html", context)


def _group_matches_by_round(matches):
    """Сгруппировать матчи по турам с дедлайном тура.

    Args:
        matches: Итерируемая последовательность матчей (уже отсортированных).

    Returns:
        list[tuple[str, list, datetime | None]]: Кортежи
        ``(название тура, матчи тура, дедлайн тура)``. Дедлайн — первый
        непустой ``deadline`` среди матчей тура (у всех матчей тура он
        обычно одинаковый).
    """
    from collections import OrderedDict

    rounds: OrderedDict[str, list] = OrderedDict()
    for m in matches:
        key = m.round_name or m.tvd_stage
        rounds.setdefault(key, []).append(m)
    result: list[tuple[str, list, object]] = []
    for name, round_matches in rounds.items():
        deadline = next((m.deadline for m in round_matches if m.deadline), None)
        result.append((name, round_matches, deadline))
    return result


def _can_manage_tournament(request, tournament):
    """Доступ к странице и действиям управления турниром.

    Клубный турнир может управлять только admin/manager этого клуба — те же
    правила, что и для редактирования турнира в панели клуба. Права staff
    платформы без роли в клубе недостаточны (иначе расходится с
    ``clubs:tournament_edit`` и создаёт риск случайных действий).

    Турнир без клуба (платформа): только сотрудники (``is_staff``).

    Args:
        request: HTTP-запрос.
        tournament: Модель турнира.

    Returns:
        bool: True, если пользователь может управлять турниром.
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return False
    if tournament.club_id:
        return user_can_manage_club(request.user, tournament.club)
    return bool(request.user.is_staff)


def _tournament_manage_get_any_tournament(request, slug):
    """Get tournament by slug and check access for common manage actions."""
    t = get_object_or_404(Tournament, slug=slug)
    if not _can_manage_tournament(request, t):
        messages.error(request, "Нет доступа к управлению этим турниром.")
        return None
    return t


def _tournament_manual_generate(tournament: Tournament) -> tuple[bool, str, str]:
    """Ручной запуск турнира без ожидания дедлайна регистрации."""
    if _is_tvd(tournament):
        return (*tvd_generate_groups(tournament), "Сформировать группы")
    if _is_fan(tournament):
        return (*fan_generate_bracket(tournament), "Сформировать сетку")
    if _is_olympic(tournament):
        return (*olympic_generate_bracket(tournament), "Сформировать сетку")
    if _is_round_robin(tournament):
        return (*round_robin_generate_bracket(tournament), "Сформировать матчи")
    return False, "Неизвестный формат турнира.", "Запустить турнир"


def _tournament_manage_primary_action(tournament: Tournament) -> tuple[bool, str]:
    """Главное ручное действие для формата турнира."""
    if tournament.status in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED):
        return False, ""
    if tournament.bracket_generated:
        return False, ""
    if _is_round_robin(tournament):
        return True, "Сформировать матчи"
    if _is_fan(tournament) or _is_olympic(tournament):
        return True, "Сформировать сетку"
    return False, ""


@login_required
def tournament_manage(request, slug):
    """Интерактивная страница управления турниром (staff или admin/manager клуба)."""
    tournament = get_object_or_404(
        Tournament.objects.prefetch_related(
            "participants__user",
            "teams__player1__user",
            "teams__player2__user",
            "matches__player1__user",
            "matches__player2__user",
            "matches__winner__user",
            "matches__team1__player1__user",
            "matches__team1__player2__user",
            "matches__team2__player1__user",
            "matches__team2__player2__user",
            "matches__winner_team__player1__user",
            "matches__winner_team__player2__user",
            "fan_results__player__user",
            "tvd_groups__members__player__user",
            "tvd_groups__members__team__player1__user",
            "tvd_groups__members__team__player2__user",
            "tvd_groups__matches__player1__user",
            "tvd_groups__matches__player2__user",
            "tvd_groups__matches__winner__user",
            "tvd_groups__matches__team1__player1__user",
            "tvd_groups__matches__team2__player1__user",
            "tvd_groups__matches__winner_team__player1__user",
        ),
        slug=slug,
    )
    if not _can_manage_tournament(request, tournament):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden("Нет доступа к управлению этим турниром.")

    is_tvd = _is_tvd(tournament)
    is_doubles = tournament.is_doubles()
    teams = []
    full_teams_count = 0
    players_for_pairs = []
    if is_doubles:
        _ensure_doubles_participants_have_teams(tournament)
        _remove_duplicate_doubles_teams(tournament)
        _remove_solo_teams_for_teamed_players(tournament)
        teams = list(
            tournament.teams.select_related("player1__user", "player2__user").order_by(
                "created_at"
            )
        )
        full_teams_count = sum(1 for t in teams if t.player2_id is not None)
        # Только игроки из одиночных заявок (ожидают партнёра); уже состоящие в паре не показываем
        solo_player_ids = {
            t.player1_id for t in teams if t.player1_id and t.player2_id is None
        }
        players_for_pairs = (
            list(
                Player.objects.filter(pk__in=solo_player_ids)
                .select_related("user")
                .order_by("user__last_name", "user__first_name")
            )
            if solo_player_ids
            else []
        )

    tvd_groups = (
        list(
            tournament.tvd_groups.prefetch_related(
                "members__player__user",
                "members__team__player1__user",
                "members__team__player2__user",
                Prefetch(
                    "matches",
                    queryset=Match.objects.select_related(
                        "player1__user",
                        "player2__user",
                        "winner__user",
                        "team1__player1__user",
                        "team1__player2__user",
                        "team2__player1__user",
                        "team2__player2__user",
                        "winner_team__player1__user",
                        "winner_team__player2__user",
                    ).order_by("round_order"),
                ),
            ).order_by("order")
        )
        if is_tvd
        else []
    )
    group_matches = [list(g.matches.all()) for g in tvd_groups]
    if is_tvd:
        tvd_main_matches = list(
            tournament.matches.filter(
                is_consolation=False,
                tvd_stage__in=(
                    "main_qf",
                    "main_sf",
                    "main_final",
                    "third_place",
                    "main_round_robin",
                    "main_rr_1_3",
                    "main_rr_4_6",
                ),
            ).order_by("round_index", "round_order")
        )
        tvd_consolation_matches = list(
            tournament.matches.filter(
                is_consolation=True,
                tvd_stage__in=(
                    "consolation_sf",
                    "consolation_final",
                    "consolation_round_robin",
                ),
            ).order_by("round_index", "round_order")
        )
    else:
        tvd_main_matches = list(
            tournament.matches.filter(is_consolation=False).order_by(
                "round_index", "round_order", "pk"
            )
        )
        tvd_consolation_matches = list(
            tournament.matches.filter(is_consolation=True).order_by(
                "round_index", "round_order", "pk"
            )
        )

    tvd_main_rounds = _group_matches_by_round(tvd_main_matches)
    tvd_consolation_rounds = _group_matches_by_round(tvd_consolation_matches)
    tvd_standings = list(
        tournament.fan_results.filter(place__isnull=False)
        .select_related("player__user")
        .order_by("place")
    )
    tvd_main_rr_standings = None
    tvd_main_rr_matrix_rows = None
    tvd_main_rr_matrix_participants = None
    tvd_cons_rr_standings = None
    tvd_cons_rr_matrix_rows = None
    tvd_cons_rr_matrix_participants = None
    main_rr_entities, main_rr_matches = get_tvd_rr_entities_and_matches(
        tournament, TVD_STAGE_MAIN_RR
    )
    if main_rr_entities and main_rr_matches:
        tvd_main_rr_standings = compute_standings_for_entities(
            tournament, main_rr_entities, main_rr_matches
        )
        tvd_main_rr_matrix_participants, main_rr_matrix = get_match_matrix_for_entities(
            tournament, main_rr_entities, main_rr_matches
        )
        standings_by_entity = {
            (row["team"] or row["player"]).id: row for row in tvd_main_rr_standings
        }
        tvd_main_rr_matrix_rows = []
        for i, p in enumerate(tvd_main_rr_matrix_participants):
            row_cells = main_rr_matrix[i] if i < len(main_rr_matrix) else []
            st = standings_by_entity.get(p.id, {})
            tvd_main_rr_matrix_rows.append(
                {
                    "participant": p,
                    "cells": row_cells,
                    "place": st.get("place"),
                    "points": st.get("points"),
                }
            )
    cons_rr_entities, cons_rr_matches = get_tvd_rr_entities_and_matches(
        tournament, TVD_STAGE_CONSOLATION_RR
    )
    if cons_rr_entities and cons_rr_matches:
        tvd_cons_rr_standings = compute_standings_for_entities(
            tournament, cons_rr_entities, cons_rr_matches
        )
        tvd_cons_rr_matrix_participants, cons_rr_matrix = get_match_matrix_for_entities(
            tournament, cons_rr_entities, cons_rr_matches
        )
        standings_by_entity = {
            (row["team"] or row["player"]).id: row for row in tvd_cons_rr_standings
        }
        tvd_cons_rr_matrix_rows = []
        for i, p in enumerate(tvd_cons_rr_matrix_participants):
            row_cells = cons_rr_matrix[i] if i < len(cons_rr_matrix) else []
            st = standings_by_entity.get(p.id, {})
            tvd_cons_rr_matrix_rows.append(
                {
                    "participant": p,
                    "cells": row_cells,
                    "place": st.get("place"),
                    "points": st.get("points"),
                }
            )
    participants = list(
        tournament.participants.select_related("user").order_by(
            "user__last_name", "user__first_name"
        )
    )

    can_generate_groups = (
        is_tvd
        and tournament.status in (TournamentStatus.UPCOMING, TournamentStatus.ACTIVE)
        and not tvd_groups
        and (
            full_teams_count >= 4
            if is_doubles
            else tournament.participants.count() >= 4
        )
    )
    groups_complete = tvd_is_group_stage_complete(tournament) if tvd_groups else False
    can_generate_playoffs = (
        is_tvd
        and tournament.status == TournamentStatus.GROUP_STAGE
        and tvd_groups
        and groups_complete
    )
    playoff_matches = tournament.matches.filter(
        is_consolation=False,
        tvd_stage__in=(
            "main_qf",
            "main_sf",
            "main_final",
            "third_place",
            "main_round_robin",
            "main_rr_1_3",
            "main_rr_4_6",
        ),
    )
    all_playoff_done = (
        playoff_matches.exists()
        and not playoff_matches.filter(status=Match.MatchStatus.SCHEDULED).exists()
    )
    can_finalize = (
        is_tvd and tournament.status == TournamentStatus.PLAYOFFS and all_playoff_done
    )
    can_generate_primary_structure, generate_primary_structure_label = (
        _tournament_manage_primary_action(tournament)
    )

    players_available_to_add = list(
        _get_players_available_to_add_queryset(tournament)[:300]
    )

    from .withdraw import get_withdrawn_player_ids, get_withdrawn_team_ids

    withdrawn_player_ids = get_withdrawn_player_ids(tournament)
    withdrawn_team_ids = get_withdrawn_team_ids(tournament)
    can_withdraw_participants = (
        _is_round_robin(tournament)
        and tournament.bracket_generated
        and tournament.status
        not in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED)
    )

    context = {
        "tournament": tournament,
        "is_doubles_tvd": is_doubles,
        "teams": teams,
        "full_teams_count": full_teams_count,
        "players_for_pairs": players_for_pairs,
        "tvd_groups": tvd_groups,
        "group_matches": group_matches,
        "tvd_main_matches": tvd_main_matches,
        "tvd_consolation_matches": tvd_consolation_matches,
        "tvd_main_rounds": tvd_main_rounds,
        "tvd_consolation_rounds": tvd_consolation_rounds,
        "tvd_standings": tvd_standings,
        "tvd_main_rr_standings": tvd_main_rr_standings,
        "tvd_main_rr_matrix_rows": tvd_main_rr_matrix_rows,
        "tvd_main_rr_matrix_participants": tvd_main_rr_matrix_participants,
        "tvd_cons_rr_standings": tvd_cons_rr_standings,
        "tvd_cons_rr_matrix_rows": tvd_cons_rr_matrix_rows,
        "tvd_cons_rr_matrix_participants": tvd_cons_rr_matrix_participants,
        "participants": participants,
        "is_tvd_manage": is_tvd,
        "can_generate_groups": can_generate_groups,
        "can_generate_playoffs": can_generate_playoffs,
        "can_finalize": can_finalize,
        "can_generate_primary_structure": can_generate_primary_structure,
        "generate_primary_structure_label": generate_primary_structure_label,
        "matches_section_title": "Плей-офф" if is_tvd else "Матчи турнира",
        "can_edit_tournament": bool(tournament.club_id)
        and tournament.status != TournamentStatus.CANCELLED,
        "can_cancel_tournament": tournament.status
        not in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED),
        "players_available_to_add": players_available_to_add,
        "withdrawn_player_ids": withdrawn_player_ids,
        "withdrawn_team_ids": withdrawn_team_ids,
        "can_withdraw_participants": can_withdraw_participants,
        "is_round_robin_manage": _is_round_robin(tournament),
    }
    context["postpayment_progress"] = get_postpayment_progress(tournament)
    context["postpayment_invoices"] = list(
        tournament.postpayment_invoices.select_related("user")
        .filter(status=TournamentPostpaymentInvoice.Status.PENDING)
        .order_by("due_at", "created_at")
    )
    if tournament.club_id:
        context["club"] = tournament.club
        context["is_club_panel"] = True
    return render(request, "tournaments/manage.html", context)


def _tournament_manage_get_tournament(request, slug):
    """Get TVD tournament by slug; check access (staff or club admin/manager); return None if no access."""
    t = _tournament_manage_get_any_tournament(request, slug)
    if t is None:
        return None
    if not _is_tvd(t):
        messages.error(request, "Управление доступно только для турниров формата ТВД.")
        return None
    return t


@login_required
def tournament_manage_generate_bracket(request, slug):
    """POST: ручной запуск турнира для любого формата без ожидания дедлайна."""
    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    pending_users = get_pending_postpayment_users(tournament)
    if (
        tournament.allow_postpayment
        and tournament.postpayment_window_started_at is None
        and pending_users
    ):
        invoice_count, fancoin_settled = open_postpayment_window(tournament)
        summary = format_postpayment_open_summary(tournament, invoice_count)
        if fancoin_settled:
            messages.success(
                request,
                f"Списано FT у {fancoin_settled} участников. {summary}",
            )
        if invoice_count:
            messages.info(
                request,
                f"Запущено окно постоплаты. {summary} "
                "Сетка будет сформирована после оплаты или по истечении срока.",
            )
        elif not fancoin_settled:
            messages.info(
                request,
                f"Окно постоплаты: {summary}",
            )
        return redirect("tournament_manage", slug=slug)
    if tournament.postpayment_window_started_at is not None:
        progress = get_postpayment_progress(tournament)
        if bool(progress["completed"]):
            ok, msg = finalize_postpayment_window(tournament)
            if ok:
                messages.success(request, msg)
            else:
                messages.warning(request, msg)
        else:
            messages.info(
                request,
                "Окно постоплаты уже запущено. Сетка будет сформирована после завершения окна.",
            )
        return redirect("tournament_manage", slug=slug)
    ok, msg, _ = _tournament_manual_generate(tournament)
    if ok:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect("tournament_manage", slug=slug)


@login_required
def tournament_manage_cancel(request, slug):
    """POST: ручная отмена турнира из панели управления."""
    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    if tournament.status == TournamentStatus.COMPLETED:
        messages.warning(request, "Завершённый турнир нельзя отменить.")
        return redirect("tournament_manage", slug=slug)
    if tournament.status == TournamentStatus.CANCELLED:
        messages.info(request, "Турнир уже отменён.")
        return redirect("tournament_manage", slug=slug)
    cancel_tournament(
        tournament,
        notify_message=(
            f"Турнир «{tournament.name}» отменён администратором. "
            "Лимит регистраций на турниры восстановлен (+1)."
        ),
    )
    messages.success(request, "Турнир отменён.")
    return redirect("tournament_manage", slug=slug)


def _player_in_full_team(tournament, player_id: int) -> bool:
    """Игрок уже в полной команде (player2 заполнен) в этом турнире."""
    return bool(
        tournament.teams.filter(
            models.Q(player1_id=player_id) | models.Q(player2_id=player_id),
            player2__isnull=False,
        ).exists()
    )


@login_required
def tournament_manage_compose_pair(request, slug):
    """POST: составить пару (два игрока → одна команда) для парного ТВД."""
    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_tournament(request, slug)
    if tournament is None or not tournament.is_doubles():
        messages.error(request, "Действие доступно только для парного ТВД.")
        return redirect("tournament_manage", slug=slug)
    if tournament.bracket_generated:
        messages.warning(request, "Группы уже сформированы, менять команды нельзя.")
        return redirect("tournament_manage", slug=slug)
    player1_id = _parse_int_post(request, "player1_id")
    player2_id = _parse_int_post(request, "player2_id")
    if not player1_id or not player2_id:
        messages.warning(request, "Выберите обоих игроков.")
        return redirect("tournament_manage", slug=slug)
    if player1_id == player2_id:
        messages.warning(request, "Выберите двух разных игроков.")
        return redirect("tournament_manage", slug=slug)
    player1 = get_object_or_404(Player, pk=player1_id)
    player2 = get_object_or_404(Player, pk=player2_id)

    solo1 = tournament.teams.filter(player1_id=player1_id, player2__isnull=True).first()
    solo2 = tournament.teams.filter(player1_id=player2_id, player2__isnull=True).first()

    if solo1:
        if _player_in_full_team(tournament, player2_id):
            messages.error(
                request,
                f"{player2.get_display_name()} уже в другой полной команде. Нельзя добавить в пару повторно.",
            )
            return redirect("tournament_manage", slug=slug)
        solo1.player2 = player2
        solo1.save(update_fields=["player2"])
        if solo2 and solo2.pk != solo1.pk:
            solo2.delete()
        messages.success(request, f"Пара составлена: {solo1.get_display_name()}.")
    elif solo2:
        if _player_in_full_team(tournament, player1_id):
            messages.error(
                request,
                f"{player1.get_display_name()} уже в другой полной команде. Нельзя добавить в пару повторно.",
            )
            return redirect("tournament_manage", slug=slug)
        solo2.player2 = player1
        solo2.save(update_fields=["player2"])
        if solo1 and solo1.pk != solo2.pk:
            solo1.delete()
        messages.success(request, f"Пара составлена: {solo2.get_display_name()}.")
    else:
        if _player_in_full_team(tournament, player1_id):
            messages.error(
                request,
                f"{player1.get_display_name()} уже в полной команде. Один игрок — одна команда в турнире.",
            )
            return redirect("tournament_manage", slug=slug)
        if _player_in_full_team(tournament, player2_id):
            messages.error(
                request,
                f"{player2.get_display_name()} уже в полной команде. Один игрок — одна команда в турнире.",
            )
            return redirect("tournament_manage", slug=slug)
        existing = tournament.teams.filter(
            models.Q(player1_id=player1_id, player2_id=player2_id)
            | models.Q(player1_id=player2_id, player2_id=player1_id),
        ).first()
        if existing:
            messages.info(request, "Такая пара уже зарегистрирована.")
        else:
            TournamentTeam.objects.create(
                tournament=tournament,
                player1=player1,
                player2=player2,
            )
            messages.success(
                request,
                f"Пара создана: {player1.get_display_name()} / {player2.get_display_name()}.",
            )
    return redirect("tournament_manage", slug=slug)


@login_required
def tournament_manage_generate_groups(request, slug):
    """POST: сформировать группы ТВД (HTMX)."""
    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    ok, msg = tvd_generate_groups(tournament)
    if ok:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect("tournament_manage", slug=slug)


@login_required
def tournament_manage_generate_playoffs(request, slug):
    """POST: сформировать плей-офф ТВД (HTMX)."""
    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    ok, msg = tvd_generate_playoffs(tournament)
    if ok:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect("tournament_manage", slug=slug)


@login_required
def tournament_manage_finalize(request, slug):
    """POST: завершить турнир ТВД (HTMX)."""
    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    ok, msg = tvd_check_and_finalize(tournament)
    if ok:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect("tournament_manage", slug=slug)


def _tvd_group_member_key(member, is_doubles):
    """Ключ участника группы для сопоставления с матчами (player_id или team_id)."""
    return member.team_id if is_doubles else member.player_id


@login_required
def tournament_manage_intermediate_results(request, slug):
    """GET: промежуточные результаты после группового этапа — кто в основную сетку, кто в утешительную."""
    tournament = _tournament_manage_get_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    tvd_groups = list(
        tournament.tvd_groups.prefetch_related(
            "members__player__user",
            "members__team__player1__user",
            "members__team__player2__user",
        ).order_by("order")
    )
    if not tvd_groups:
        messages.warning(request, "Группы ещё не сформированы.")
        return redirect("tournament_manage", slug=slug)
    if not tvd_is_group_stage_complete(tournament):
        messages.warning(
            request, "Групповой этап ещё не завершён. Завершите все матчи групп."
        )
        return redirect("tournament_manage", slug=slug)
    group_count = len(tvd_groups)
    is_doubles = tournament.is_doubles()
    group_tables = []
    main_advancing = []
    consolation_advancing = []
    for group in tvd_groups:
        group_matches = list(
            Match.objects.filter(
                tournament=tournament,
                tvd_group=group,
                tvd_stage=TVD_STAGE_GROUP,
                status__in=(Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER),
            )
            .select_related("player1", "player2", "team1", "team2")
            .prefetch_related("result_proposals")
        )
        member_has_rt = {}
        member_rating_delta = {}
        for m in group_matches:
            if is_doubles:
                e1, e2 = m.team1_id, m.team2_id
            else:
                e1, e2 = m.player1_id, m.player2_id
            if e1 is None or e2 is None:
                continue
            member_rating_delta[e1] = member_rating_delta.get(e1, 0) + (
                m.rating_delta_player1 or 0
            )
            member_rating_delta[e2] = member_rating_delta.get(e2, 0) + (
                m.rating_delta_player2 or 0
            )
            if not m.winner_id and not m.winner_team_id:
                continue
            if m.is_walkover_loss():
                winner_id = m.winner_team_id if is_doubles else m.winner_id
                loser_id = e2 if winner_id == e1 else e1
                member_has_rt[loser_id] = True
        members = list(group.members.order_by("final_place", "seed"))
        rows = []
        for m in members:
            key = _tvd_group_member_key(m, is_doubles)
            place = m.final_place or 0
            display_name = m.get_entity_display()
            has_win = m.wins > 0
            has_rt = member_has_rt.get(key, False)
            rating_delta_sum = member_rating_delta.get(key, 0)
            if place == 1 or place == 2:
                dest = "main"
                main_advancing.append(
                    {
                        "group_name": group.name,
                        "place": place,
                        "display_name": display_name,
                        "has_win": has_win,
                        "has_rt": has_rt,
                        "rating_delta_sum": rating_delta_sum,
                    }
                )
            elif place == 3 and group_count >= 3:
                dest = "consolation"
                consolation_advancing.append(
                    {
                        "group_name": group.name,
                        "display_name": display_name,
                        "has_win": has_win,
                        "has_rt": has_rt,
                        "rating_delta_sum": rating_delta_sum,
                    }
                )
            else:
                dest = None
            rows.append(
                {
                    "place": place,
                    "display_name": display_name,
                    "wins": m.wins,
                    "losses": m.losses,
                    "games": f"{m.games_won}:{m.games_lost}",
                    "games_won": m.games_won,
                    "games_lost": m.games_lost,
                    "destination": dest,
                    "has_win": has_win,
                    "has_rt": has_rt,
                    "rating_delta_sum": rating_delta_sum,
                }
            )
        group_tables.append({"group": group, "rows": rows})
    can_create_main = tournament.status != TournamentStatus.PLAYOFFS
    has_consolation_matches = Match.objects.filter(
        tournament=tournament, is_consolation=True
    ).exists()
    can_create_consolation = (
        group_count >= 3 and consolation_advancing and not has_consolation_matches
    )
    context = {
        "tournament": tournament,
        "group_tables": group_tables,
        "main_advancing": main_advancing,
        "consolation_advancing": consolation_advancing,
        "group_count": group_count,
        "has_consolation": group_count >= 3,
        "not_playoffs_yet": can_create_main or can_create_consolation,
        "can_create_main": can_create_main,
        "can_create_consolation": can_create_consolation,
    }
    return render(request, "tournaments/manage_intermediate_results.html", context)


@login_required
def tournament_manage_intermediate_generate_main(request, slug):
    """POST: создать только основную сетку (формат olympic или circular)."""
    if request.method != "POST":
        return redirect("tournament_manage_intermediate_results", slug=slug)
    tournament = _tournament_manage_get_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    bracket_format = (request.POST.get("format") or "olympic").strip().lower()
    if bracket_format not in ("olympic", "circular"):
        bracket_format = "olympic"
    ok, msg = tvd_generate_main_bracket(tournament, bracket_format=bracket_format)
    if ok:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect("tournament_manage_intermediate_results", slug=slug)


@login_required
def tournament_manage_intermediate_generate_consolation(request, slug):
    """POST: создать только утешительную сетку (формат olympic или circular)."""
    if request.method != "POST":
        return redirect("tournament_manage_intermediate_results", slug=slug)
    tournament = _tournament_manage_get_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    bracket_format = (request.POST.get("format") or "olympic").strip().lower()
    if bracket_format not in ("olympic", "circular"):
        bracket_format = "olympic"
    ok, msg = tvd_generate_consolation_bracket(
        tournament, bracket_format=bracket_format
    )
    if ok:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect("tournament_manage_intermediate_results", slug=slug)


def _get_players_available_to_add_queryset(tournament):
    """QuerySet игроков, которых можно добавить в турнир (до формирования групп)."""
    if getattr(tournament, "bracket_generated", False):
        return Player.objects.none()
    base_queryset = Player.objects.filter(user__isnull=False)
    if tournament.club_id:
        base_queryset = base_queryset.filter(
            user__club_memberships__club_id=tournament.club_id,
            user__club_memberships__status=ClubMemberStatus.ACTIVE,
        ).distinct()
    if tournament.is_doubles():
        full_team_player_ids = set(
            tournament.teams.filter(player2__isnull=False).values_list(
                "player1_id", "player2_id"
            )
        )
        flat = set()
        for a, b in full_team_player_ids:
            if a:
                flat.add(a)
            if b:
                flat.add(b)
        flat |= set(
            tournament.teams.filter(player2__isnull=True).values_list(
                "player1_id", flat=True
            )
        )
        return (
            base_queryset.exclude(pk__in=flat)
            .select_related("user")
            .order_by("user__last_name", "user__first_name")
        )
    participants_ids = tournament.participants.values_list("pk", flat=True)
    return (
        base_queryset.exclude(pk__in=participants_ids)
        .select_related("user")
        .order_by("user__last_name", "user__first_name")
    )


@login_required
def tournament_manage_search_participants(request, slug):
    """GET ?q=... — поиск участников по имени или телефону для добавления в турнир. JSON."""
    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        from django.http import JsonResponse

        return JsonResponse({"results": []})
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        from django.http import JsonResponse

        return JsonResponse({"results": []})
    # Фильтрация в Python: в SQLite LOWER() не переводит кириллицу в нижний регистр
    q_lower = q.lower()
    candidates = list(_get_players_available_to_add_queryset(tournament)[:500])
    results = []
    for p in candidates:
        if len(results) >= 50:
            break
        last = (p.user.last_name or "").lower()
        first = (p.user.first_name or "").lower()
        phone = (p.user.phone or "").lower()
        if q_lower in last or q_lower in first or q_lower in phone:
            uc_str = f"{p.ntrp_level:.1f}" if p.ntrp_level is not None else "—"
            rating_str = f"{p.total_points:.0f}" if p.total_points is not None else "—"
            results.append(
                {
                    "id": p.pk,
                    "display": f"{p.get_display_name()} (УС: {uc_str}, Р: {rating_str}) — {p.user.phone or '—'}",
                }
            )
    from django.http import JsonResponse

    return JsonResponse({"results": results})


def _tournament_manage_player_already_in(tournament, player) -> tuple[bool, str | None]:
    """Проверить, что игрок уже в турнире. Возвращает (already_in, error_message)."""
    if tournament.is_doubles():
        if tournament.teams.filter(
            player1_id=player.pk, player2__isnull=False
        ).exists():
            return True, "Игрок уже в полной команде в этом турнире."
        if tournament.teams.filter(player2_id=player.pk).exists():
            return True, "Игрок уже в полной команде в этом турнире."
        if tournament.teams.filter(player1_id=player.pk, player2__isnull=True).exists():
            return True, "Игрок уже добавлен (ожидает партнёра)."
        return False, None
    else:
        if tournament.participants.filter(pk=player.pk).exists():
            return True, "Игрок уже в списке участников."
        return False, None


@login_required
def tournament_manage_add_participant(request, slug):
    """POST: добавить участника по player_id. При отсутствии оплаты — редирект на страницу выбора."""
    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    player_id = _parse_int_post(request, "player_id")
    if not player_id:
        messages.warning(request, "Выберите участника.")
        return redirect("tournament_manage", slug=slug)
    player = get_object_or_404(Player, pk=player_id)
    already, err = _tournament_manage_player_already_in(tournament, player)
    if already:
        messages.warning(request, err)
        return redirect("tournament_manage", slug=slug)
    if _tournament_requires_entry_payment(tournament):
        if not TournamentEntryPayment.objects.filter(
            tournament=tournament, user_id=player.user_id
        ).exists():
            return redirect(
                "tournament_manage_add_participant_confirm",
                slug=slug,
                player_id=player.pk,
            )
    _do_add_participant_to_tournament(tournament, player)
    messages.success(request, f"Участник добавлен: {player.get_display_name()}.")
    return redirect("tournament_manage", slug=slug)


def _do_add_participant_to_tournament(tournament, player):
    """Добавить участника в турнир (participants или solo-команда)."""
    if tournament.is_doubles():
        TournamentTeam.objects.create(
            tournament=tournament, player1=player, player2=None
        )
    else:
        tournament.participants.add(player)


@login_required
def tournament_manage_add_participant_confirm(request, slug, player_id):
    """Страница выбора: участник не оплатил — добавить всё равно или отправить уведомление об оплате."""
    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    player = get_object_or_404(Player, pk=player_id)
    if not _tournament_requires_entry_payment(tournament):
        _do_add_participant_to_tournament(tournament, player)
        messages.success(
            request,
            f"Участник добавлен: {player.get_display_name()}.",
        )
        return redirect("tournament_manage", slug=slug)
    if tournament.is_doubles():
        already = tournament.teams.filter(
            models.Q(player1_id=player_id) | models.Q(player2_id=player_id)
        ).exists()
    else:
        already = tournament.participants.filter(pk=player_id).exists()
    if already:
        messages.info(request, "Участник уже добавлен.")
        return redirect("tournament_manage", slug=slug)
    context = {
        "tournament": tournament,
        "player": player,
    }
    return render(request, "tournaments/manage_add_participant_confirm.html", context)


@login_required
def tournament_manage_add_participant_force(request, slug):
    """POST: добавить участника без проверки оплаты (player_id)."""
    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    player_id = _parse_int_post(request, "player_id")
    if not player_id:
        messages.warning(request, "Выберите участника.")
        return redirect("tournament_manage", slug=slug)
    player = get_object_or_404(Player, pk=player_id)
    if tournament.is_doubles():
        if tournament.teams.filter(player1_id=player_id, player2__isnull=True).exists():
            messages.info(request, "Игрок уже добавлен (ожидает партнёра).")
            return redirect("tournament_manage", slug=slug)
        if (
            tournament.teams.filter(player1_id=player_id)
            .filter(player2__isnull=False)
            .exists()
            or tournament.teams.filter(player2_id=player_id).exists()
        ):
            messages.warning(request, "Игрок уже в полной команде.")
            return redirect("tournament_manage", slug=slug)
    else:
        if tournament.participants.filter(pk=player_id).exists():
            messages.info(request, "Игрок уже в списке участников.")
            return redirect("tournament_manage", slug=slug)
    _do_add_participant_to_tournament(tournament, player)
    messages.success(
        request,
        f"Участник добавлен без проверки оплаты: {player.get_display_name()}.",
    )
    return redirect("tournament_manage", slug=slug)


@login_required
def tournament_manage_send_payment_notification(request, slug):
    """POST: отправить участнику уведомление со ссылкой на оплату (player_id). После оплаты он будет добавлен автоматически."""
    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    player_id = _parse_int_post(request, "player_id")
    if not player_id:
        messages.warning(request, "Выберите участника.")
        return redirect("tournament_manage", slug=slug)
    player = get_object_or_404(Player, pk=player_id)
    payment_url = reverse("payment_preview") + f"?type=tournament&id={tournament.id}"
    if tournament.is_doubles():
        msg = (
            f"Администратор приглашает вас оплатить участие в турнире «{tournament.name}». "
            "После оплаты завершите регистрацию на странице турнира (выберите партнёра или создайте команду)."
        )
    else:
        msg = (
            f"Администратор приглашает вас оплатить участие в турнире «{tournament.name}». "
            "После оплаты вы будете добавлены в турнир автоматически."
        )
    Notification.objects.create(
        user=player.user,
        message=msg,
        url=payment_url,
    )
    if tournament.is_doubles():
        messages.success(
            request,
            f"Уведомление об оплате отправлено {player.get_display_name()}. "
            "После оплаты участник сможет завершить регистрацию на странице турнира.",
        )
    else:
        messages.success(
            request,
            f"Уведомление об оплате отправлено {player.get_display_name()}. "
            "После оплаты участник будет добавлен в турнир автоматически.",
        )
    return redirect("tournament_manage", slug=slug)


def _build_refund_feedback_url(
    refund: TournamentEntryRefundRequest, request=None
) -> str:
    """Ссылка на форму обратной связи с подставленной темой и текстом для возврата (путь или absolute_uri)."""
    from urllib.parse import quote

    subject = f"Возврат взноса — турнир «{refund.tournament.name}»"
    user = refund.user
    user_display = user.get_display_name() or f"ID {user.pk}"
    removed_str = (
        refund.removed_at.strftime("%d.%m.%Y %H:%M") if refund.removed_at else "—"
    )
    message = (
        f"Заявка на возврат: {refund.refund_ref}\n"
        f"Участник: {user_display}, User ID: {user.pk}\n"
        f"Турнир: «{refund.tournament.name}», ID: {refund.tournament_id}\n"
        f"Сумма к возврату: {refund.amount} руб.\n"
        f"Дата удаления с турнира: {removed_str}\n\n"
        "Прошу вернуть средства за участие в турнире."
    )
    path = (
        reverse("support_feedback")
        + f"?subject={quote(subject)}&message={quote(message)}"
    )
    if request:
        return str(request.build_absolute_uri(path))
    from django.conf import settings

    base = getattr(settings, "SITE_URL", None) or ""
    return str((base.rstrip("/") + path) if base else path)


def _notify_removed_participant_refund(
    request, user, tournament, refund: TournamentEntryRefundRequest
):
    """Уведомление в ЛК и в Telegram о снятии с турнира и возврате взноса."""
    ref_url = _build_refund_feedback_url(refund, request)
    msg_lk = (
        f"Вы сняты с турнира «{tournament.name}». Взнос за участие был оплачен. "
        "Для возврата средств обратитесь к администратору через форму обратной связи."
    )
    try:
        Notification.objects.create(user=user, message=msg_lk, url=ref_url)
    except Exception as e:
        logger.exception(
            "Notification create failed for removed participant refund (user=%s): %s",
            user.pk,
            e,
        )
    try:
        from apps.telegram_bot import notifications as tg

        tg.notify_tournament_removed_refund(user, tournament, ref_url)
    except Exception as e:
        logger.warning("notify_tournament_removed_refund telegram: %s", e)


def _notify_removed_participant_no_refund(request, user, tournament):
    """Уведомление в ЛК о снятии с турнира (без возврата — взнос не оплачивался)."""
    tour_url = request.build_absolute_uri(
        reverse("tournament_detail", args=[tournament.slug])
    )
    msg_lk = f"Вы сняты с турнира «{tournament.name}»."
    try:
        Notification.objects.create(user=user, message=msg_lk, url=tour_url)
    except Exception as e:
        logger.exception(
            "Notification create failed for removed participant (user=%s): %s",
            user.pk,
            e,
        )


@login_required
def tournament_manage_remove_participant(request, slug):
    """POST: удалить участника (player_id для одиночного, team_id для парного). При оплаченном взносе — заявка на возврат и уведомление."""
    import secrets

    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    if getattr(tournament, "bracket_generated", False):
        messages.error(
            request, "Нельзя удалять участников после формирования групп/сетки."
        )
        return redirect("tournament_manage", slug=slug)
    team_id = _parse_int_post(request, "team_id")
    player_id = _parse_int_post(request, "player_id")
    entry_fee = (tournament.entry_fee or 0) if hasattr(tournament, "entry_fee") else 0
    removed_users = []  # list of (user, had_paid)

    if tournament.is_doubles() and team_id:
        team = get_object_or_404(TournamentTeam, pk=team_id, tournament=tournament)
        for p in [team.player1, team.player2]:
            if p:
                had_paid = TournamentEntryPayment.objects.filter(
                    tournament=tournament, user_id=p.user_id
                ).exists()
                removed_users.append((p.user, had_paid))
        team.delete()
    elif not tournament.is_doubles() and player_id:
        player = get_object_or_404(Player, pk=player_id)
        if not tournament.participants.filter(pk=player_id).exists():
            messages.warning(request, "Участник не найден в турнире.")
            return redirect("tournament_manage", slug=slug)
        had_paid = TournamentEntryPayment.objects.filter(
            tournament=tournament, user_id=player.user_id
        ).exists()
        removed_users.append((player.user, had_paid))
        tournament.participants.remove(player)
    else:
        messages.warning(request, "Укажите участника или команду для удаления.")
        return redirect("tournament_manage", slug=slug)

    for user, had_paid in removed_users:
        if had_paid and entry_fee and float(entry_fee) > 0:
            refund_ref = "REF-" + secrets.token_urlsafe(8).upper()[:10]
            while TournamentEntryRefundRequest.objects.filter(
                refund_ref=refund_ref
            ).exists():
                refund_ref = "REF-" + secrets.token_urlsafe(8).upper()[:10]
            refund = TournamentEntryRefundRequest.objects.create(
                tournament=tournament,
                user=user,
                amount=entry_fee,
                refund_ref=refund_ref,
            )
            _notify_removed_participant_refund(request, user, tournament, refund)
        else:
            _notify_removed_participant_no_refund(request, user, tournament)

    messages.success(request, "Участник(и) удалены из турнира.")
    return redirect("tournament_manage", slug=slug)


@login_required
def tournament_manage_withdraw_participant(request, slug):
    """POST: снять участника после старта кругового турнира (walkover + условный FT)."""
    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")

    from .withdraw import withdraw_participant

    team_id = _parse_int_post(request, "team_id")
    player_id = _parse_int_post(request, "player_id")
    if tournament.is_doubles() and team_id:
        team = get_object_or_404(TournamentTeam, pk=team_id, tournament=tournament)
        ok, msg = withdraw_participant(tournament, team=team, withdrawn_by=request.user)
    elif not tournament.is_doubles() and player_id:
        player = get_object_or_404(Player, pk=player_id)
        ok, msg = withdraw_participant(
            tournament, player=player, withdrawn_by=request.user
        )
    else:
        messages.warning(request, "Укажите участника или команду для снятия.")
        return redirect("tournament_manage", slug=slug)

    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect("tournament_manage", slug=slug)


def _parse_int_post(request, key, default=None):
    val = request.POST.get(key)
    if val is None or str(val).strip() == "":
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


@login_required
def tournament_manage_match_result(request, slug, pk):
    """POST: внести результат матча (score1/score2, winner_id/winner_team_id). Опционально walkover=1 — тех. результат (неявка/просрочка)."""
    from django.utils import timezone

    from .proposal_service import notify_participants_match_result_confirmed

    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    match = get_object_or_404(Match, tournament=tournament, pk=pk)
    if tournament.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED):
        messages.warning(
            request,
            "В отмененном или завершенном турнире изменение результатов недоступно.",
        )
        return redirect("tournament_manage", slug=slug)
    is_walkover = request.POST.get("walkover") in ("1", "on", "true", "yes")
    score1 = _parse_int_post(request, "score1")
    score2 = _parse_int_post(request, "score2")
    set2_p1 = _parse_int_post(request, "set2_p1")
    set2_p2 = _parse_int_post(request, "set2_p2")
    set3_p1 = _parse_int_post(request, "set3_p1")
    set3_p2 = _parse_int_post(request, "set3_p2")
    winner_id = _parse_int_post(request, "winner_id")
    winner_team_id = _parse_int_post(request, "winner_team_id")
    is_doubles_match = match.team1_id and match.team2_id
    if is_doubles_match:
        if not winner_team_id or winner_team_id not in (match.team1_id, match.team2_id):
            messages.warning(request, "Укажите победившую команду.")
            return redirect("tournament_manage", slug=slug)
        winner_team = get_object_or_404(
            TournamentTeam, pk=winner_team_id, tournament=tournament
        )
        match.winner_team = winner_team
        match.winner = winner_team.player1
    else:
        if not winner_id and match.player1_id and match.player2_id:
            messages.warning(request, "Укажите победителя.")
            return redirect("tournament_manage", slug=slug)
        winner = None
        if winner_id:
            winner = get_object_or_404(Player, pk=winner_id)
            if winner.id != match.player1_id and winner.id != match.player2_id:
                messages.warning(
                    request, "Победитель должен быть одним из игроков матча."
                )
                return redirect("tournament_manage", slug=slug)
        match.winner = winner
    if is_walkover:
        match.status = Match.MatchStatus.WALKOVER
        match.completed_datetime = timezone.now()
        match.rating_status = Match.RatingCalcStatus.PENDING
        if match.winner_id == match.player1_id or (
            is_doubles_match and match.winner_team_id == match.team1_id
        ):
            match.player1_set1 = 6
            match.player2_set1 = 0
            match.player1_set2 = 6
            match.player2_set2 = 0
        else:
            match.player1_set1 = 0
            match.player2_set1 = 6
            match.player1_set2 = 0
            match.player2_set2 = 6
        match.save()
        try:
            notify_participants_match_result_confirmed(match, walkover=True)
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                "notify_participants_match_result_confirmed after admin walkover: %s", e
            )
        messages.success(
            request, "Технический результат (RT) сохранён. Таблица и сетка обновлены."
        )
    else:
        if score1 is not None and score2 is not None:
            match.player1_set1 = score1
            match.player2_set1 = score2
        if set2_p1 is not None and set2_p2 is not None:
            match.player1_set2 = set2_p1
            match.player2_set2 = set2_p2
        if set3_p1 is not None and set3_p2 is not None:
            match.player1_set3 = set3_p1
            match.player2_set3 = set3_p2
        # Проверка согласованности: победитель должен выиграть больше сетов по введённому счёту
        winner_entity_id = (
            match.winner_id
            if not is_doubles_match
            else (match.winner_team.player1_id if match.winner_team else None)
        )
        sets_p1 = sum(
            1
            for a, b in [
                (match.player1_set1, match.player2_set1),
                (match.player1_set2, match.player2_set2),
                (match.player1_set3, match.player2_set3),
            ]
            if a is not None and b is not None and a > b
        )
        sets_p2 = sum(
            1
            for a, b in [
                (match.player1_set1, match.player2_set1),
                (match.player1_set2, match.player2_set2),
                (match.player1_set3, match.player2_set3),
            ]
            if a is not None and b is not None and b > a
        )
        winner_won_more = (
            (winner_entity_id == match.player1_id and sets_p1 > sets_p2)
            or (winner_entity_id == match.player2_id and sets_p2 > sets_p1)
            or (
                is_doubles_match
                and match.winner_team_id == match.team1_id
                and sets_p1 > sets_p2
            )
            or (
                is_doubles_match
                and match.winner_team_id == match.team2_id
                and sets_p2 > sets_p1
            )
        )
        if (sets_p1 + sets_p2) >= 1 and not winner_won_more:
            messages.error(
                request,
                "Счёт не соответствует выбранному победителю: по введённым геймам выигрывает другой участник. "
                "Проверьте порядок полей: слева — первый игрок (сверху), справа — второй (снизу). "
                "Если вы ввели счёт верно, но перепутали колонки — внесите снова, поменяв числа местами.",
            )
            return redirect("tournament_manage", slug=slug)
        match.status = Match.MatchStatus.COMPLETED
        match.completed_datetime = timezone.now()
        match.rating_status = Match.RatingCalcStatus.PENDING
        match.save()
        try:
            notify_participants_match_result_confirmed(match, walkover=False)
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                "notify_participants_match_result_confirmed after admin result: %s", e
            )
        messages.success(
            request, "Результат сохранён. Участникам отправлены уведомления."
        )
    return redirect("tournament_manage", slug=slug)


@login_required
def tournament_manage_match_deadline(request, slug, pk):
    """POST: вручную изменить дедлайн незавершённого матча на странице управления."""
    from datetime import datetime, time

    from django.utils import timezone

    if request.method != "POST":
        return redirect("tournament_manage", slug=slug)
    tournament = _tournament_manage_get_any_tournament(request, slug)
    if tournament is None:
        return redirect("tournament_list")
    match = get_object_or_404(Match, tournament=tournament, pk=pk)
    if tournament.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED):
        messages.warning(
            request,
            "В отменённом или завершённом турнире изменение дедлайна недоступно.",
        )
        return redirect("tournament_manage", slug=slug)
    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        messages.warning(
            request,
            "Нельзя изменить дедлайн у завершённого матча.",
        )
        return redirect("tournament_manage", slug=slug)

    raw = (request.POST.get("deadline") or "").strip()
    if not raw:
        messages.warning(request, "Укажите дату дедлайна.")
        return redirect("tournament_manage", slug=slug)
    try:
        deadline_date = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        messages.warning(request, "Некорректная дата дедлайна.")
        return redirect("tournament_manage", slug=slug)

    old_deadline = match.deadline
    old_date = timezone.localtime(old_deadline).date() if old_deadline else None
    if old_date is not None and deadline_date == old_date:
        messages.info(
            request,
            "Дедлайн не изменился — уведомления участникам не отправлялись.",
        )
        return redirect("tournament_manage", slug=slug)

    match.deadline = timezone.make_aware(
        datetime.combine(deadline_date, time(23, 59, 59))
    )
    match.save(update_fields=["deadline"])

    from .utils import notify_participants_match_deadline_changed

    notified, emailed = notify_participants_match_deadline_changed(
        match,
        old_deadline=old_deadline,
        new_deadline=match.deadline,
    )
    messages.success(
        request,
        (
            f"Дедлайн матча обновлён: до {deadline_date.strftime('%d.%m.%Y')}. "
            f"Уведомления отправлены участникам "
            f"(личный кабинет: {notified}, email: {emailed})."
        ),
    )
    return redirect("tournament_manage", slug=slug)


def tournament_tables_list(request):
    """Страница «Турнирные таблицы» — список турниров с краткой статистикой."""
    city = (request.GET.get("city") or "").strip()
    status = (request.GET.get("status") or "").strip()
    club_filter = (request.GET.get("club") or "").strip()

    tournaments = (
        Tournament.objects.all()
        .select_related("club")
        .prefetch_related(
            "participants__user",
            "matches",
            "fan_results",
            "allowed_categories",
        )
    )
    if city:
        tournaments = filter_field_contains_ci(
            tournaments, "city", city, annotation="_tables_list_city_l"
        )
    if status:
        tournaments = tournaments.filter(status=status)
    if club_filter == CLUB_FILTER_PLATFORM:
        tournaments = tournaments.filter(club__isnull=True)
    elif club_filter == CLUB_FILTER_CLUB_ONLY:
        tournaments = tournaments.filter(club__isnull=False)
    elif club_filter:
        tournaments = tournaments.filter(club__slug=club_filter)

    tournaments = list(tournaments.order_by("-start_date"))
    # Добавляем статистику для каждого турнира
    for t in tournaments:
        main_matches = t.matches.filter(is_consolation=False)
        matches_total = main_matches.count()
        matches_completed = main_matches.filter(
            status__in=["completed", "walkover"]
        ).count()
        t.participants_count = t.participants.count()
        t.matches_total = matches_total
        t.matches_completed = matches_completed
        t.matches_pending = matches_total - matches_completed
        t.progress_pct = (
            int(100 * matches_completed / matches_total) if matches_total > 0 else 0
        )
    context = {
        "tournaments": tournaments,
        "current_city": city,
        "current_status": status,
        "tables_list_club_filter": club_filter,
        "club_filter_choices": club_filter_choices_for_tournament_lists(),
    }
    return render(request, "tournaments/tables_list.html", context)


@login_required_with_message(
    "Детали турнирной таблицы доступны только для зарегистрированных пользователей."
)
def tournament_tables_detail(request, slug):
    """Детальная страница турнирной таблицы: графики, диаграммы, полная статистика."""
    tournament = get_object_or_404(
        Tournament.objects.select_related("club").prefetch_related(
            "matches__player1__user",
            "matches__player2__user",
            "matches__winner__user",
            "matches__team1__player1__user",
            "matches__team1__player2__user",
            "matches__team2__player1__user",
            "matches__team2__player2__user",
            "participants__user",
            "fan_results__player__user",
            "allowed_categories",
        ),
        slug=slug,
    )
    is_fan = _is_fan(tournament)
    is_olympic = _is_olympic(tournament)
    is_tvd = _is_tvd(tournament)
    is_round_robin = _is_round_robin(tournament)
    if tournament.is_doubles():
        participants = []
        for team in tournament.teams.filter(player2__isnull=False).select_related(
            "player1__user", "player2__user"
        ):
            participants.extend([team.player1, team.player2])
        participants = list({p.id: p for p in participants}.values())
        participants.sort(key=lambda p: -p.total_points)
    else:
        participants = list(
            tournament.participants.select_related("user").order_by("-total_points")
        )

    if is_round_robin:
        rr_standings = compute_standings(tournament)
        if tournament.status == "completed":
            fan_results_map = {
                r.player_id: r.fan_points
                for r in tournament.fan_results.select_related("player")
            }
        else:
            fan_results_map = {}
        standings = [
            {
                "place": row["place"],
                "player": row["player"],
                "team": row.get("team"),
                "fan_result": None,
                "fan_points": row["points"],
                "round_eliminated": "—",
                "rating_points": fan_results_map.get(
                    row["team"].player1_id if row.get("team") else row["player"].id
                ),
                "matches": row.get("matches"),
                "wins": row.get("wins"),
                "losses": row.get("losses"),
                "sets": row.get("sets"),
                "games": row.get("games"),
            }
            for row in rr_standings
        ]
    elif is_olympic:
        # Олимпийская система: таблица по итоговому месту (place из TournamentPlayerResult)
        results_by_place = list(
            tournament.fan_results.filter(place__isnull=False)
            .select_related("player__user")
            .order_by("place")
        )
        if tournament.is_doubles():
            # Парные: по одному ряду на команду (у обоих игроков одинаковые place и fan_points)
            seen_places = set()
            standings = []
            for r in results_by_place:
                if r.place in seen_places:
                    continue
                seen_places.add(r.place)
                team = (
                    tournament.teams.filter(
                        Q(player1=r.player) | Q(player2=r.player), player2__isnull=False
                    )
                    .select_related("player1__user", "player2__user")
                    .first()
                )
                standings.append(
                    {
                        "place": r.place,
                        "player": team.player1 if team else r.player,
                        "team": team,
                        "fan_result": r,
                        "fan_points": r.fan_points,
                        "round_eliminated": f"Место {r.place}",
                    }
                )
        else:
            standings = [
                {
                    "place": r.place,
                    "player": r.player,
                    "team": None,
                    "fan_result": r,
                    "fan_points": r.fan_points,
                    "round_eliminated": f"Место {r.place}",
                }
                for r in results_by_place
            ]
    elif is_tvd:
        # ТВД: таблица по итоговому месту (place из TournamentPlayerResult), как в итогах турнира
        results_by_place = list(
            tournament.fan_results.filter(place__isnull=False)
            .select_related("player__user")
            .order_by("place")
        )
        standings = [
            {
                "place": r.place,
                "player": r.player,
                "team": None,
                "fan_result": r,
                "fan_points": r.fan_points,
                "round_eliminated": f"Место {r.place}",
            }
            for r in results_by_place
        ]
    else:
        fan_results = {}
        if is_fan:
            for r in tournament.fan_results.select_related("player__user"):
                fan_results[r.player_id] = r
        participants_sorted = sorted(
            participants,
            key=lambda p: (
                -(fan_results.get(p.id).fan_points if fan_results.get(p.id) else 0),
                -p.total_points,
            ),
        )
        standings = []
        for i, p in enumerate(participants_sorted, 1):
            fr = fan_results.get(p.id)
            standings.append(
                {
                    "place": i,
                    "player": p,
                    "fan_result": fr,
                    "fan_points": fr.fan_points if fr else 0,
                    "round_eliminated": (
                        fr.get_round_eliminated_display() if fr else "—"
                    ),
                }
            )

    # Матчи по раундам
    matches = tournament.matches.order_by(
        "is_consolation", "round_index", "round_order"
    )
    matches_by_round = []
    for k, group in groupby(matches, key=lambda m: (m.round_name, m.is_consolation)):
        matches_by_round.append((k[0], k[1], list(group)))

    # Статистика для графиков
    main_matches = tournament.matches.filter(is_consolation=False)
    status_counts = defaultdict(int)
    for m in main_matches:
        status_counts[m.status or "scheduled"] += 1
    chart_status_labels = []
    chart_status_data = []
    status_display = {
        "completed": "Завершён",
        "walkover": "Без игры",
        "scheduled": "Запланирован",
        "in_progress": "В процессе",
        "cancelled": "Отменён",
    }
    for status in ["completed", "walkover", "scheduled", "in_progress", "cancelled"]:
        if status_counts[status] > 0:
            chart_status_labels.append(status_display.get(status, status))
            chart_status_data.append(status_counts[status])

    # Распределение очков по раундам вылета (для FAN и ТВД)
    round_points = defaultdict(int)
    if is_fan or is_tvd:
        for r in tournament.fan_results.all():
            round_points[r.round_eliminated] += 1
    chart_round_labels = []
    chart_round_data = []
    round_display = {
        "winner": "Победитель",
        "final": "Финалист",
        "sf": "Полуфинал",
        "r2": "2 круг",
        "r1": "1 круг",
    }
    for rk in ["winner", "final", "sf", "r2", "r1"]:
        if round_points[rk] > 0:
            chart_round_labels.append(round_display.get(rk, rk))
            chart_round_data.append(round_points[rk])

    # Рейтинг участников (для гистограммы) — подписи = имена, не «Место N»
    participants_by_rating = sorted(
        [p for p in participants if p.total_points],
        key=lambda p: -float(p.total_points or 0),
    )[:20]
    ratings_sorted = [float(p.total_points) for p in participants_by_rating]
    ratings_labels = [str(p) for p in participants_by_rating]

    dashboard = build_tables_dashboard(
        tournament,
        is_round_robin=is_round_robin,
        is_fan=is_fan,
        is_tvd=is_tvd,
        participants=participants,
        chart_status_labels=chart_status_labels,
        chart_status_data=chart_status_data,
        chart_round_labels=chart_round_labels,
        chart_round_data=chart_round_data,
        ratings_labels=ratings_labels,
        ratings_sorted=ratings_sorted,
    )
    tables_charts_config = json.dumps(dashboard.charts, ensure_ascii=False)

    context = {
        "tournament": tournament,
        "is_fan": is_fan,
        "is_olympic": is_olympic,
        "is_tvd": is_tvd,
        "is_round_robin": is_round_robin,
        "participants": participants,
        "standings": standings,
        "matches_by_round": matches_by_round,
        "show_chart_status": dashboard.show_flags["show_chart_status"],
        "show_chart_rounds": dashboard.show_flags["show_chart_rounds"],
        "show_chart_ratings": dashboard.show_flags["show_chart_ratings"],
        "show_chart_timeline": dashboard.show_flags["show_chart_timeline"],
        "show_chart_sets": dashboard.show_flags["show_chart_sets"],
        "show_chart_character": dashboard.show_flags["show_chart_character"],
        "show_chart_deltas": dashboard.show_flags["show_chart_deltas"],
        "show_insights": dashboard.show_flags["show_insights"],
        "show_heatmap": dashboard.show_flags["show_heatmap"],
        "tables_charts_config": tables_charts_config,
        "insights": dashboard.insights,
        "heatmap": dashboard.heatmap,
        "participants_count": dashboard.kpi["participants_count"],
        "matches_total": dashboard.kpi["matches_total"],
        "matches_completed": dashboard.kpi["matches_completed"],
        "progress_pct": dashboard.kpi["progress_pct"],
        "kpi_sets_played": dashboard.kpi["sets_played"],
        "kpi_games_total": dashboard.kpi["games_total"],
        "kpi_avg_games": dashboard.kpi["avg_games"],
        "kpi_three_set_pct": dashboard.kpi["three_set_pct"],
        "kpi_tiebreaks": dashboard.kpi["tiebreaks"],
        "kpi_walkovers": dashboard.kpi["walkovers"],
    }
    return render(request, "tournaments/tables_detail.html", context)


def champions_league(request):
    """Champions League page."""
    tournaments = (
        Tournament.objects.filter(tournament_type=TournamentType.CHAMPIONS_LEAGUE)
        .prefetch_related("allowed_categories")
        .order_by("-start_date")
    )
    return render(
        request, "tournaments/champions_league.html", {"tournaments": tournaments}
    )


@login_required_with_message(
    "Детали матча доступны только для зарегистрированных пользователей."
)
def match_detail(request, pk):
    """Match detail page. Только для авторизованных пользователей."""
    match = get_object_or_404(
        Match.objects.select_related(
            "player1__user",
            "player2__user",
            "winner__user",
            "tournament",
            "tournament__club",
            "court",
            "team1__player1__user",
            "team1__player2__user",
            "team2__player1__user",
            "team2__player2__user",
            "sparring_response__sparring_request",
        ).prefetch_related("tournament__allowed_categories"),
        pk=pk,
    )
    # Сезонные очки за матч: проигравший получает очки при вылете (FAN/Olympic)
    season_points_p1 = None
    season_points_p2 = None
    show_season_pts = (
        match.tournament_id
        and (_is_fan(match.tournament) or _is_olympic(match.tournament))
        and match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER)
    )
    if show_season_pts:
        p1_for_result = match.team1.player1 if match.team1_id else match.player1
        p2_for_result = match.team2.player1 if match.team2_id else match.player2
        # Оба игрока могут иметь TournamentPlayerResult: проигравший — при вылете,
        # победитель финала — при завершении турнира (fan_points_winner)
        if p1_for_result:
            r1 = TournamentPlayerResult.objects.filter(
                tournament=match.tournament, player=p1_for_result
            ).first()
            season_points_p1 = r1.fan_points if r1 else 0
        if p2_for_result:
            r2 = TournamentPlayerResult.objects.filter(
                tournament=match.tournament, player=p2_for_result
            ).first()
            season_points_p2 = r2.fan_points if r2 else 0

    player = getattr(request.user, "player", None)
    fallback_url = reverse("my_matches")
    if match.tournament_id:
        fallback_url = reverse(
            "tournament_detail", kwargs={"slug": match.tournament.slug}
        )
    next_url = _get_safe_next_url(request, fallback_url)
    pending_proposals = list(
        match.result_proposals.filter(
            status=Match.ProposalStatus.PENDING
        ).select_related("proposer")
    )
    can_view_match_actions = bool(player and player in _match_participants(match))
    result_order_blocker = (
        find_blocking_earlier_tournament_match(match, player)
        if player and can_view_match_actions
        else None
    )
    result_order_block_message = (
        format_tournament_match_order_block_message(result_order_blocker, player)
        if result_order_blocker and player
        else ""
    )
    can_submit_result = bool(
        can_view_match_actions
        and match.status
        not in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER)
        and not pending_proposals
        and not result_order_blocker
    )

    # Соперники для блока контактов (только для участников матча)
    opponents: list = []
    if player and can_view_match_actions:
        from apps.tournaments.utils import get_match_opponents_for_player

        opponents = get_match_opponents_for_player(match, player)

    return render(
        request,
        "tournaments/match_detail.html",
        {
            "match": match,
            "player": player,
            "pending_proposals": pending_proposals,
            "can_view_match_actions": can_view_match_actions,
            "can_submit_result": can_submit_result,
            "result_order_block_message": result_order_block_message,
            "next_url": next_url,
            "is_fan": _is_fan(match.tournament),
            "is_olympic": _is_olympic(match.tournament),
            "season_points_player1": season_points_p1,
            "season_points_player2": season_points_p2,
            "opponents": opponents,
            **_get_club_panel_context_for_tournament(request, match.tournament),
        },
    )


@login_required
def my_matches(request):
    """Страница «Мои матчи»: сначала список турниров/спаррингов, затем матчи выбранной группы."""

    player = getattr(request.user, "player", None)
    if player is None:
        player = Player.objects.create(user=request.user)

    tournament_slug = (request.GET.get("tournament") or "").strip()
    is_sparring_group = request.GET.get("sparring") == "1"
    search_query = (request.GET.get("q") or "").strip()
    kind_filter = (request.GET.get("kind") or "").strip()

    base_q = (
        models.Q(player1=player)
        | models.Q(player2=player)
        | models.Q(team1__player1=player)
        | models.Q(team1__player2=player)
        | models.Q(team2__player1=player)
        | models.Q(team2__player2=player)
    )

    # Детальный режим: показываем матчи конкретного турнира или все спарринги
    if tournament_slug or is_sparring_group:
        matches_qs = Match.objects.filter(base_q).select_related(
            "player1__user",
            "player2__user",
            "tournament",
            "team1__player1__user",
            "team1__player2__user",
            "team2__player1__user",
            "team2__player2__user",
            "sparring_response__sparring_request",
        )
        current_tournament: Tournament | None = None
        if tournament_slug:
            matches_qs = matches_qs.filter(
                tournament__slug=tournament_slug,
                tournament__club__isnull=True,
                match_type=Match.MatchType.TOURNAMENT,
            )
            current_tournament = Tournament.objects.filter(
                slug=tournament_slug,
                club__isnull=True,
            ).first()
        else:
            matches_qs = matches_qs.filter(match_type=Match.MatchType.SPARRING)

        matches = list(order_player_matches_for_display(matches_qs))

        proposals = MatchResultProposal.objects.filter(
            match__in=matches
        ).select_related("proposer", "match")
        pending_by_match: dict[int, list[MatchResultProposal]] = {}
        for p in proposals:
            if p.status == Match.ProposalStatus.PENDING:
                pending_by_match.setdefault(p.match_id, []).append(p)

        attach_tournament_result_order_flags(matches, player)
        for m in matches:
            m.pending_proposals = pending_by_match.get(m.id, [])
            m.has_pending = bool(m.pending_proposals)

        try:
            from apps.player_ratings.services import get_match_rating_status
        except Exception:
            get_match_rating_status = None

        if get_match_rating_status is not None and request.user.is_authenticated:
            for m in matches:
                m.rating_status = get_match_rating_status(m, player)

        return render(
            request,
            "tournaments/my_matches.html",
            {
                "matches": matches,
                "player": player,
                "current_tournament": current_tournament,
                "is_sparring_group": is_sparring_group,
                "search_query": search_query,
                "kind_filter": kind_filter,
            },
        )

    # Режим списка турниров/спаррингов
    participant_matches = Match.objects.filter(base_q).select_related(
        "tournament",
    )

    tournament_matches = participant_matches.filter(
        match_type=Match.MatchType.TOURNAMENT,
        tournament__isnull=False,
        tournament__club__isnull=True,
    )

    tournaments_qs = (
        Tournament.objects.filter(matches__in=tournament_matches)
        .distinct()
        .annotate(
            match_count=Count(
                "matches", filter=models.Q(matches__in=tournament_matches)
            ),
            nearest_deadline=Min("matches__deadline"),
        )
        .order_by("-start_date")
    )
    if search_query:
        sq = (search_query or "").strip()
        if sq:
            sq_l = sq.lower()
            tournaments_qs = tournaments_qs.annotate(_mm_city_l=Lower("city")).filter(
                Q(name__icontains=search_query) | Q(_mm_city_l__contains=sq_l)
            )

    tournaments = list(tournaments_qs)
    sparring_count = (
        participant_matches.filter(match_type=Match.MatchType.SPARRING)
        .distinct()
        .count()
    )

    if kind_filter == "tournaments":
        show_sparring_group = False
    elif kind_filter == "sparring":
        tournaments = []
        show_sparring_group = sparring_count > 0
    else:
        show_sparring_group = sparring_count > 0

    return render(
        request,
        "tournaments/my_matches.html",
        {
            "matches": [],
            "player": player,
            "tournaments": tournaments,
            "sparring_match_count": sparring_count,
            "show_sparring_group": show_sparring_group,
            "search_query": search_query,
            "kind_filter": kind_filter,
        },
    )


@login_required
def my_sparring_matches(request):
    """List sparring matches (personal meetings) for current player."""

    player = getattr(request.user, "player", None)
    if player is None:
        player = Player.objects.create(user=request.user)

    matches_qs = Match.objects.filter(
        models.Q(player1=player) | models.Q(player2=player),
        match_type=Match.MatchType.SPARRING,
    ).select_related(
        "player1__user",
        "player2__user",
        "sparring_response__sparring_request",
    )
    matches = list(order_player_matches_for_display(matches_qs))

    proposals = MatchResultProposal.objects.filter(match__in=matches).select_related(
        "proposer", "match"
    )
    # Group all pending proposals by match_id
    pending_by_match = {}
    for p in proposals:
        if p.status == Match.ProposalStatus.PENDING:
            if p.match_id not in pending_by_match:
                pending_by_match[p.match_id] = []
            pending_by_match[p.match_id].append(p)

    for m in matches:
        m.pending_proposals = pending_by_match.get(m.id, [])
        m.has_pending = len(m.pending_proposals) > 0

    return render(
        request,
        "tournaments/my_sparring_matches.html",
        {
            "matches": matches,
            "player": player,
        },
    )


@login_required
def propose_result(request, pk):
    """Propose result for a match by participant."""
    fallback_url = reverse("my_matches")

    match = get_object_or_404(
        Match.objects.select_related(
            "team1__player1__user",
            "team1__player2__user",
            "team2__player1__user",
            "team2__player2__user",
            "player1",
            "player2",
        ),
        pk=pk,
    )
    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        messages.info(request, "Матч уже завершён.")
        return redirect(_get_safe_next_url(request, fallback_url))

    if request.method != "POST":
        return redirect(_get_safe_next_url(request, fallback_url))
    player = getattr(request.user, "player", None)
    if player is None:
        messages.error(request, "Создайте профиль игрока, чтобы предложить результат.")
        return redirect("profile_edit")

    if player not in _match_participants(match):
        messages.error(request, "Вы не участвуете в этом матче.")
        return redirect(_get_safe_next_url(request, fallback_url))

    if match.tournament_id:
        from .withdraw import is_player_withdrawn

        if is_player_withdrawn(match.tournament, player):
            messages.error(
                request,
                "Вы сняты с турнира и не можете вносить результаты матчей.",
            )
            return redirect(_get_safe_next_url(request, fallback_url))

    if match.result_proposals.filter(status=Match.ProposalStatus.PENDING).exists():
        messages.info(
            request,
            "По этому матчу уже внесён результат.",
        )
        return redirect(_get_safe_next_url(request, fallback_url))

    blocking_match = find_blocking_earlier_tournament_match(match, player)
    if blocking_match:
        messages.error(
            request,
            format_tournament_match_order_block_message(blocking_match, player),
        )
        return redirect(_get_safe_next_url(request, fallback_url))

    result = request.POST.get("result") or Match.ResultChoice.WIN

    def _to_int(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    score_fields = {
        "player1_set1": _to_int(request.POST.get("p1s1")),
        "player1_set2": _to_int(request.POST.get("p1s2")),
        "player1_set3": _to_int(request.POST.get("p1s3")),
        "player2_set1": _to_int(request.POST.get("p2s1")),
        "player2_set2": _to_int(request.POST.get("p2s2")),
        "player2_set3": _to_int(request.POST.get("p2s3")),
    }

    walkover_results = (
        Match.ResultChoice.WALKOVER_WIN,
        Match.ResultChoice.WALKOVER_LOSS,
    )
    if result not in walkover_results:
        try:
            # Победитель определяется по счёту, а не по выпадающему списку
            result = derive_proposer_result_from_score(
                match,
                player,
                **score_fields,
            )
        except ProposalValidationError as exc:
            messages.error(request, str(exc))
            return redirect(_get_safe_next_url(request, fallback_url))

    proposal = MatchResultProposal.objects.create(
        match=match,
        proposer=player,
        result=result,
        **score_fields,
    )

    # Сразу применяем результат без ожидания подтверждения вторым игроком
    try:
        apply_proposal(proposal)
    except ProposalValidationError as exc:
        proposal.delete()
        messages.error(request, str(exc))
        return redirect(_get_safe_next_url(request, fallback_url))

    for opponent_user in get_match_opponent_users(match, player):
        if match.tournament:
            match_context = f"в турнире {match.tournament.name}"
        elif match.is_sparring():
            match_context = "спаррингового матча"
        else:
            match_context = "матча"

        Notification.objects.create(
            user=opponent_user,
            message=f"{player} внёс результат {match_context}. Матч завершён.",
            url=reverse("match_detail", args=[match.pk]),
        )

    try:
        from apps.telegram_bot import notifications as tg

        tg.notify_result_proposal(proposal)
    except Exception:
        pass

    messages.success(request, "Результат сохранён. Матч завершён.")
    return redirect(_get_safe_next_url(request, fallback_url))


@login_required
def confirm_proposal(request, pk):
    """Opponent confirms or rejects proposal."""
    fallback_url = reverse("my_matches")

    proposal = get_object_or_404(
        MatchResultProposal.objects.select_related(
            "match__player1",
            "match__player2",
            "proposer",
            "match__team1__player1",
            "match__team1__player2",
            "match__team2__player1",
            "match__team2__player2",
        ),
        pk=pk,
    )

    if request.method != "POST":
        return redirect(_get_safe_next_url(request, fallback_url))

    match = proposal.match
    player = getattr(request.user, "player", None)
    if player is None or player not in _match_participants(match):
        messages.error(request, "Вы не участвуете в этом матче.")
        return redirect(_get_safe_next_url(request, fallback_url))

    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        messages.info(request, "Матч уже завершён.")
        return redirect(_get_safe_next_url(request, fallback_url))

    if proposal.status != Match.ProposalStatus.PENDING:
        messages.info(request, "Этот результат уже обработан.")
        return redirect(_get_safe_next_url(request, fallback_url))

    if proposal.proposer == player:
        messages.error(request, "Вы не можете подтверждать свой же запрос.")
        return redirect(_get_safe_next_url(request, fallback_url))

    opponent_users = get_match_opponent_users(match, proposal.proposer)
    if request.user not in opponent_users:
        messages.error(
            request,
            "Подтвердить результат может только соперник (в парной игре — игрок противоположной команды).",
        )
        return redirect(_get_safe_next_url(request, fallback_url))

    action = request.POST.get("action")
    if action == "accept":
        # Передаём в сигнал ленты активности, кто именно подтвердил результат.
        proposal._confirmed_by_user = request.user
        apply_proposal(proposal)
        # FAN-логика (advance, consolation, finalize) вызывается из post_save сигнала Match
        # Уведомления в ЛК и Telegram (с рейтингом и силой) создаются внутри apply_proposal для обоих участников
        messages.success(request, "Результат подтверждён.")
    else:
        # Telegram-уведомление инициатору (до удаления proposal)
        try:
            from apps.telegram_bot.notifications import notify_proposal_rejected

            notify_proposal_rejected(proposal)
        except Exception:
            pass  # Telegram-уведомление не критично
        # Фиксируем отклонение результата в ленте активности до удаления заявки.
        try:
            from apps.core.activity import log_activity
            from apps.core.models import PlatformActivityEvent

            log_activity(
                event_type=PlatformActivityEvent.EventType.MATCH_RESULT_REJECTED,
                actor=request.user,
                description=f"Отклонил результат матча: {match}",
                target_url=reverse("match_detail", args=[match.pk]),
                dedupe_key=f"proposal_rejected:{proposal.pk}",
            )
        except Exception:
            pass  # запись в ленту не должна мешать основному сценарию
        proposal.delete()
        Notification.objects.create(
            user=proposal.proposer.user,
            message=f"{player} отклонил результат матча. Введите свой результат.",
            url=reverse("my_matches"),
        )
        messages.info(request, "Результат отклонён. Введите свой результат матча.")

    return redirect(_get_safe_next_url(request, fallback_url))


def _tournament_requires_entry_payment(tournament) -> bool:
    """Турнир с вступительным взносом: при регистрации возможна оплата взноса (если нет подписки/лимита)."""
    fee = getattr(tournament, "entry_fee", None) or 0
    return bool(fee and float(fee) > 0)


# Сообщение для редиректа на страницу «оплатить взнос или оформить подписку»
REGISTER_PAY_OR_SUBSCRIBE_MSG = (
    "Для регистрации оплатите вступительный взнос или оформите подписку."
)
REGISTER_PAY_CLUB_ENTRY_FEE_MSG = (
    "Для регистрации на турнир клуба нужно оплатить вступительный взнос."
)
REGISTER_CLUB_MEMBERSHIP_REQUIRED_MSG = (
    "Чтобы записаться на турнир клуба, вступите в клуб организатора."
)


def _tournament_does_not_consume_subscription_limit(tournament) -> bool:
    """Турниры, которые не должны списывать глобальный лимит регистраций."""
    return bool(
        tournament.club_id
        or tournament.is_one_day
        or (
            _tournament_requires_entry_payment(tournament)
            and not tournament_allows_postpayment_registration(tournament)
        )
    )


def _build_tournament_payment_redirect(tournament, *, next_url: str = ""):
    """Формирует redirect на оплату вступительного взноса турнира."""
    params = {"type": "tournament", "id": tournament.id}
    if next_url:
        params["next"] = next_url
    return redirect(f"{reverse('payment_preview')}?{urlencode(params)}")


def _check_club_fee_access(user, tournament):
    """Проверяет, не заблокирован ли доступ к турниру из-за неоплаченного взноса."""
    tournament_member = _get_tournament_club_member(user, tournament)
    if not tournament_member:
        return True, None

    fee = (
        ClubMembershipFee.objects.filter(
            club_id=tournament.club_id,
            is_active=True,
            restrict_tournament_access=True,
        )
        .order_by("-id")
        .first()
    )
    if not fee:
        return True, None

    fee_status = get_fee_status_for_member(tournament_member.club, tournament_member)
    if fee_status == "paid" or fee_status is None:
        return True, None

    return (
        False,
        "Регистрация на турниры клуба недоступна до оплаты членского взноса за текущий период.",
    )


def _check_tournament_registration_eligibility(request, tournament, player):
    """Проверка подписки, категории и лимитов для регистрации. Возвращает (ok, error_message)."""
    user = getattr(request, "user", None)
    if user is None:
        return False, "Требуется авторизация."

    try:
        sub = user.subscription
    except Exception:
        sub = None

    is_admin = user.is_superuser or user.is_staff

    # Проверка категорий применяется ко ВСЕМ, включая администраторов
    allowed_categories = list(
        tournament.allowed_categories.values_list("category", flat=True)
    )
    if not allowed_categories:
        return (
            False,
            "В турнире не указаны допустимые категории участников. Обратитесь к организатору.",
        )
    if player.skill_level not in allowed_categories:
        from apps.users.models import SkillLevel

        allowed_labels = [SkillLevel(c).label for c in allowed_categories]
        player_label = SkillLevel(player.skill_level).label
        return (
            False,
            f"Регистрация на этот турнир разрешена только для категорий: {', '.join(allowed_labels)}. "
            f"Ваша категория «{player_label}» не входит в список. "
            "Если ваш уровень изменился, пройдите тест уровня силы или обратитесь в поддержку.",
        )

    # Администраторы обходят проверку подписки и лимитов
    if is_admin:
        return True, None

    if tournament.club_id and not tournament.is_open_interclub:
        if not _get_tournament_club_member(user, tournament):
            return False, REGISTER_CLUB_MEMBERSHIP_REQUIRED_MSG

    can_access_by_fee, fee_error = _check_club_fee_access(user, tournament)
    if not can_access_by_fee:
        return False, fee_error

    tournament_member = _get_tournament_club_member(user, tournament)
    if tournament_member:
        club_eligibility = check_club_tournament_registration_eligibility(
            tournament_member,
            tournament,
        )
        if club_eligibility.mode == RegistrationMode.BLOCKED:
            return False, club_eligibility.message
        if club_eligibility.mode == RegistrationMode.PAID:
            return False, REGISTER_PAY_CLUB_ENTRY_FEE_MSG
        return True, None

    # Однодневные: подписка не обязательна (взнос оплачивается отдельно)
    if tournament.is_one_day:
        return True, None

    # Многодневные с взносом: по подписке в рамках лимита — бесплатно; иначе — оплата взноса или оформление подписки
    if _tournament_requires_entry_payment(tournament):
        if sub and sub.has_fancoin(TOURNAMENT_REGISTRATION_COST):
            return True, None
        if tournament_allows_postpayment_registration(tournament):
            return True, None
        return False, REGISTER_PAY_OR_SUBSCRIBE_MSG

    # Многодневные без взноса: нужен активный безлимит или несгораемый остаток регистраций.
    if not sub or not sub.has_fancoin(TOURNAMENT_REGISTRATION_COST):
        return (
            False,
            "Для участия в многодневных турнирах нужна активная подписка и минимум 3 FT.",
        )

    return True, None


def _check_user_can_register_for_tournament(user, tournament):
    """Проверка возможности регистрации пользователя (для партнёра)."""

    class Req:
        pass

    r = Req()
    r.user = user
    try:
        p = user.player
    except Exception:
        p = Player.objects.filter(user=user).first()
    if not p:
        return False, "У пользователя нет профиля игрока."
    return _check_tournament_registration_eligibility(r, tournament, p)


def _is_player_registered_in_doubles(tournament, player):
    """Проверка: зарегистрирован ли игрок в парном турнире (в любой команде)."""
    return tournament.teams.filter(Q(player1=player) | Q(player2=player)).exists()


def _ensure_doubles_participants_have_teams(tournament):
    """
    Для парного турнира: у каждого участника из M2M participants должна быть команда.
    Участники, добавленные через админку в participants, получают solo-команду (player2=null),
    чтобы отображаться в GUI и в форме «Составить пару».
    """
    if not tournament.is_doubles():
        return
    for player in tournament.participants.all():
        if not tournament.teams.filter(Q(player1=player) | Q(player2=player)).exists():
            TournamentTeam.objects.get_or_create(
                tournament=tournament,
                player1=player,
                defaults={},
            )


def _remove_duplicate_doubles_teams(tournament):
    """
    Удалить дубликаты полных команд: одна пара игроков (A, B) — одна команда.
    Оставляем команду с меньшим pk, остальные с той же парой удаляем.
    """
    if not tournament.is_doubles():
        return
    full_teams = list(
        tournament.teams.filter(player2__isnull=False).values_list(
            "pk", "player1_id", "player2_id"
        )
    )
    seen_pairs = set()
    for pk, p1, p2 in full_teams:
        key = (min(p1, p2), max(p1, p2))
        if key in seen_pairs:
            TournamentTeam.objects.filter(pk=pk).delete()
        else:
            seen_pairs.add(key)


def _remove_solo_teams_for_teamed_players(tournament):
    """
    Удалить solo-команды (ожидает партнёра) для игроков, которые уже в полной команде.
    Один игрок не может одновременно быть в полной команде и «ожидать партнёра».
    """
    if not tournament.is_doubles():
        return
    players_in_full_teams = set(
        tournament.teams.filter(player2__isnull=False).values_list(
            "player1_id", flat=True
        )
    ) | set(
        tournament.teams.filter(player2__isnull=False).values_list(
            "player2_id", flat=True
        )
    )
    tournament.teams.filter(
        player2__isnull=True,
        player1_id__in=players_in_full_teams,
    ).delete()


def _get_tournament_club_member(user, tournament) -> ClubMember | None:
    """Возвращает активное членство пользователя в клубе-организаторе турнира.

    Args:
        user: Текущий пользователь.
        tournament: Турнир для проверки членства.

    Returns:
        ClubMember | None: Активное членство или None.
    """
    if not tournament.club_id or not user.is_authenticated:
        return None
    member = (
        ClubMember.objects.filter(
            club_id=tournament.club_id,
            user=user,
            status=ClubMemberStatus.ACTIVE,
        )
        .select_related("club")
        .first()
    )
    return cast(ClubMember | None, member)


@login_required
@require_filled_profile
@require_verified_player
def tournament_register(request, slug):
    """Register authenticated user to a tournament."""

    tournament = get_object_or_404(Tournament, slug=slug)
    player = getattr(request.user, "player", None)
    if player is None:
        player = Player.objects.create(user=request.user)

    if tournament.club_id and not tournament.is_open_interclub:
        if not _get_tournament_club_member(request.user, tournament):
            messages.error(request, REGISTER_CLUB_MEMBERSHIP_REQUIRED_MSG)
            return redirect("tournament_detail", slug=tournament.slug)

    if tournament.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED):
        messages.error(
            request, "Регистрация недоступна: турнир уже завершён или отменён."
        )
        return redirect("tournament_detail", slug=tournament.slug)

    if getattr(tournament, "bracket_generated", False):
        messages.error(request, "Регистрация закрыта: сетка турнира уже сформирована.")
        return redirect("tournament_detail", slug=tournament.slug)
    if getattr(tournament, "postpayment_window_started_at", None):
        messages.error(
            request,
            "Регистрация закрыта: запущено окно постоплаты и формирование сетки.",
        )
        return redirect("tournament_detail", slug=tournament.slug)

    if tournament.is_full():
        messages.error(request, "Регистрация закрыта: все места заняты.")
        return redirect("tournament_detail", slug=tournament.slug)

    # Парный турнир — отдельный поток регистрации
    if tournament.is_doubles():
        return redirect("tournament_register_doubles", slug=tournament.slug)

    # Check gender compatibility
    # "open" — любой пол (открытая категория), "mixed" — микст (для парных: М+Ж в команде)
    if tournament.gender not in ("mixed", "open"):
        if (tournament.gender == "male" and player.gender != "male") or (
            tournament.gender == "female" and player.gender != "female"
        ):
            gender_text = "мужской" if tournament.gender == "male" else "женский"
            messages.error(request, f"Этот турнир только для {gender_text} категории.")
            return redirect("tournament_detail", slug=tournament.slug)

    # Check if player is already registered
    if tournament.participants.filter(id=player.id).exists():
        messages.info(request, "Вы уже зарегистрированы на этот турнир.")
        return redirect("tournament_detail", slug=tournament.slug)

    tournament_member = _get_tournament_club_member(request.user, tournament)

    # SUBSCRIPTION CHECK
    try:
        sub = request.user.subscription
    except Exception:
        sub = None

    # Проверка всех условий регистрации (включая категории)
    ok, err = _check_tournament_registration_eligibility(request, tournament, player)
    if not ok:
        messages.error(request, err)
        if err == REGISTER_PAY_CLUB_ENTRY_FEE_MSG:
            return _build_tournament_payment_redirect(tournament)
        if err == REGISTER_PAY_OR_SUBSCRIBE_MSG:
            return redirect("tournament_register_required", slug=tournament.slug)
        if "подписк" in (err or ""):
            return redirect("pricing")
        return redirect("tournament_detail", slug=tournament.slug)

    is_admin = request.user.is_superuser or request.user.is_staff

    # Однодневный с взносом: все переходят на страницу оплаты
    if _tournament_requires_entry_payment(tournament) and tournament.is_one_day:
        return _build_tournament_payment_redirect(tournament)
    else:
        # Без взноса: регистрация сразу; лимит подписки не тратим для однодневных
        if is_admin:
            messages.success(
                request, "Регистрация администратора (бесплатно/безлимитно)."
            )
        else:
            if not _tournament_does_not_consume_subscription_limit(tournament):
                try:
                    sub = request.user.subscription
                    if sub and sub.spend_fancoin(
                        TOURNAMENT_REGISTRATION_COST,
                        reason=FancoinTransaction.Reason.TOURNAMENT_REGISTRATION,
                        tournament=tournament,
                    ):
                        mark_registration_covered(
                            tournament,
                            request.user,
                            TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT,
                        )
                        messages.success(
                            request,
                            f"Вы зарегистрированы! Баланс FT: {sub.get_fancoin_balance()}",
                        )
                    else:
                        messages.success(request, "Вы зарегистрированы!")
                except Exception:
                    messages.success(request, "Вы зарегистрированы!")
            else:
                messages.success(request, "Вы зарегистрированы!")

        if tournament_member:
            consumed, consume_error = consume_member_tournament_limit(
                tournament_member,
                tournament,
            )
            if not consumed:
                messages.error(request, consume_error)
                return redirect("tournament_detail", slug=tournament.slug)
            mark_registration_covered(
                tournament,
                request.user,
                TournamentRegistrationCoverage.CoverageType.CLUB_PLAN_SLOT,
            )

        tournament.participants.add(player)
        try:
            from apps.core.telegram_notify import notify_tournament_registration
            from apps.telegram_bot import notifications as tg

            notify_tournament_registration(request.user, tournament)
            tg.notify_tournament_registered(request.user, tournament)
        except Exception:
            pass
        return redirect("tournament_detail", slug=tournament.slug)


@login_required
@require_filled_profile
@require_verified_player
def tournament_register_required(request, slug):
    """Страница выбора: оплатить вступительный взнос или оформить подписку (для многодневного турнира)."""
    tournament = get_object_or_404(Tournament, slug=slug)
    if tournament.is_one_day:
        return redirect("tournament_register", slug=slug)
    fee = (tournament.entry_fee or 0) if hasattr(tournament, "entry_fee") else 0
    from urllib.parse import urlencode

    from django.urls import reverse

    payment_url = (
        reverse("payment_preview")
        + "?"
        + urlencode({"type": "tournament", "id": tournament.id})
    )
    return render(
        request,
        "tournaments/register_required.html",
        {
            "tournament": tournament,
            "entry_fee": fee,
            "payment_url": payment_url,
        },
    )


@login_required
@require_filled_profile
@require_verified_player
def tournament_register_doubles(request, slug):
    """Регистрация на парный турнир: solo, с партнёром или присоединение к существующей паре."""

    tournament = get_object_or_404(Tournament, slug=slug)
    if not tournament.is_doubles():
        return redirect("tournament_register", slug=slug)

    player = getattr(request.user, "player", None)
    if player is None:
        player = Player.objects.create(user=request.user)

    if tournament.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED):
        messages.error(
            request, "Регистрация недоступна: турнир уже завершён или отменён."
        )
        return redirect("tournament_detail", slug=tournament.slug)

    if tournament.bracket_generated:
        messages.error(request, "Регистрация закрыта: сетка турнира уже сформирована.")
        return redirect("tournament_detail", slug=slug)
    if getattr(tournament, "postpayment_window_started_at", None):
        messages.error(
            request,
            "Регистрация закрыта: запущено окно постоплаты и формирование сетки.",
        )
        return redirect("tournament_detail", slug=slug)

    if tournament.is_full():
        messages.error(request, "Регистрация закрыта: все места заняты.")
        return redirect("tournament_detail", slug=slug)

    # "open" — любой пол (открытая категория), "mixed" — микст (для парных: М+Ж в команде)
    if tournament.gender not in ("mixed", "open"):
        if (tournament.gender == "male" and player.gender != "male") or (
            tournament.gender == "female" and player.gender != "female"
        ):
            gender_text = "мужской" if tournament.gender == "male" else "женский"
            messages.error(request, f"Этот турнир только для {gender_text} категории.")
            return redirect("tournament_detail", slug=slug)

    if _is_player_registered_in_doubles(tournament, player):
        messages.info(request, "Вы уже зарегистрированы на этот турнир.")
        return redirect("tournament_detail", slug=slug)

    if tournament.club_id and not tournament.is_open_interclub:
        if not _get_tournament_club_member(request.user, tournament):
            messages.error(request, REGISTER_CLUB_MEMBERSHIP_REQUIRED_MSG)
            return redirect("tournament_detail", slug=tournament.slug)

    tournament_member = _get_tournament_club_member(request.user, tournament)

    ok, err = _check_tournament_registration_eligibility(request, tournament, player)
    if not ok:
        messages.error(request, err)
        if err == REGISTER_PAY_CLUB_ENTRY_FEE_MSG:
            return _build_tournament_payment_redirect(
                tournament,
                next_url=request.build_absolute_uri(
                    reverse("tournament_register_doubles", kwargs={"slug": slug})
                ),
            )
        if err == REGISTER_PAY_OR_SUBSCRIBE_MSG:
            return redirect("tournament_register_required", slug=slug)
        if err and "подписк" in err:
            return redirect("pricing")
        return redirect("tournament_detail", slug=slug)

    # Однодневный с взносом: без оплаты (по сессии) не показываем форму — редирект на оплату
    if _tournament_requires_entry_payment(tournament) and tournament.is_one_day:
        paid_ids = request.session.get("tournament_entry_paid") or []
        if tournament.id not in paid_ids:
            return _build_tournament_payment_redirect(
                tournament,
                next_url=request.build_absolute_uri(
                    reverse("tournament_register_doubles", kwargs={"slug": slug})
                ),
            )

    solo_teams = list(
        tournament.teams.filter(player2__isnull=True).select_related("player1__user")
    )

    # Для микст-турниров фильтруем команды по противоположному полу
    if tournament.is_mixed_doubles() and player.gender:
        solo_teams = [
            t
            for t in solo_teams
            if t.player1.gender and t.player1.gender != player.gender
        ]

    partner_search_results = []

    if request.method == "GET" and request.GET.get("q"):
        q = request.GET.get("q", "").strip()
        if q:
            from django.db.models import CharField, Q, Value
            from django.db.models.functions import Coalesce, Concat

            filters = (
                Q(user__first_name__icontains=q)
                | Q(user__last_name__icontains=q)
                | Q(user__email__icontains=q)
                | Q(user__phone__icontains=q)
                | Q(_full_name__icontains=q)
            )
            if str(q).isdigit():
                filters |= Q(id=int(q))

            partner_queryset = (
                Player.objects.filter(is_bye=False)
                .exclude(id=player.id)
                .annotate(
                    _full_name=Concat(
                        Coalesce("user__first_name", Value("")),
                        Value(" "),
                        Coalesce("user__last_name", Value("")),
                        output_field=CharField(),
                    )
                )
                .filter(filters)
            )
            if tournament.club_id:
                partner_queryset = partner_queryset.filter(
                    user__club_memberships__club=tournament.club,
                    user__club_memberships__status=ClubMemberStatus.ACTIVE,
                )

            all_results = list(
                partner_queryset.select_related("user")
                .distinct()
                .order_by("user__last_name", "user__first_name")[:10]
            )

            partner_search_results = all_results

            # Для микст-турниров фильтруем результаты поиска по противоположному полу
            if tournament.is_mixed_doubles():
                if not player.gender:
                    messages.warning(
                        request,
                        "Для участия в микст-турнире укажите свой пол в профиле.",
                    )
                    partner_search_results = []
                else:
                    # Фильтруем по противоположному полу
                    filtered_results = []
                    for p in all_results:
                        if not p.gender:
                            continue  # Пропускаем игроков без указанного пола
                        if p.gender != player.gender:
                            filtered_results.append(p)
                    partner_search_results = filtered_results

                    if not partner_search_results:
                        if all_results:
                            gender_text = (
                                "женщину" if player.gender == "male" else "мужчину"
                            )
                            messages.info(
                                request,
                                f"Найдено игроков: {len(all_results)}, но для микст-турнира нужен партнёр противоположного пола ({gender_text}).",
                            )
                        else:
                            messages.info(
                                request, f"По запросу «{q}» игроки не найдены."
                            )
            else:
                # Для обычных парных турниров показываем сообщение если ничего не найдено
                if not partner_search_results:
                    messages.info(
                        request,
                        f"По запросу «{q}» игроки не найдены. Попробуйте другой поиск.",
                    )

    # POST: обработка выбора
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "solo":
            if tournament_member:
                consumed, consume_error = consume_member_tournament_limit(
                    tournament_member,
                    tournament,
                )
                if not consumed:
                    messages.error(request, consume_error)
                    return redirect("tournament_detail", slug=tournament.slug)
            TournamentTeam.objects.create(tournament=tournament, player1=player)
            if not _tournament_does_not_consume_subscription_limit(tournament):
                try:
                    sub = request.user.subscription
                    if sub and sub.spend_fancoin(
                        TOURNAMENT_REGISTRATION_COST,
                        reason=FancoinTransaction.Reason.TOURNAMENT_REGISTRATION,
                        tournament=tournament,
                    ):
                        mark_registration_covered(
                            tournament,
                            request.user,
                            TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT,
                        )
                except Exception:
                    pass
            paid_ids = request.session.get("tournament_entry_paid") or []
            if tournament.id in paid_ids:
                paid_ids = [i for i in paid_ids if i != tournament.id]
                request.session["tournament_entry_paid"] = paid_ids
                request.session.modified = True
            try:
                from apps.core.telegram_notify import notify_tournament_registration
                from apps.telegram_bot import notifications as tg

                notify_tournament_registration(request.user, tournament)
                tg.notify_tournament_registered(request.user, tournament)
            except Exception:
                pass
            messages.success(
                request,
                "Вы зарегистрированы. Партнёр может присоединиться к вам со своей страницы турнира.",
            )
            return redirect("tournament_detail", slug=slug)

        if action == "join" and solo_teams:
            team_id = request.POST.get("team_id")
            team = next((t for t in solo_teams if t.id == int(team_id or 0)), None)
            if team:
                return _do_join_team(request, tournament, player, team)
            messages.error(request, "Команда не найдена.")
        elif action == "add_partner":
            partner_id = request.POST.get("partner_id")
            if partner_id:
                return _do_add_partner(request, tournament, player, partner_id)
            messages.error(request, "Укажите партнёра.")

    context = {
        "tournament": tournament,
        "solo_teams": solo_teams,
        "partner_search_results": partner_search_results,
        "persist_messages": True,
    }
    context.update(_get_club_panel_context_for_tournament(request, tournament))
    return render(request, "tournaments/register_doubles.html", context)


def _do_join_team(request, tournament, player, team):
    """Присоединить игрока к существующей команде (solo)."""
    # Проверка всех условий регистрации (включая категории)
    ok, err = _check_tournament_registration_eligibility(request, tournament, player)
    if not ok:
        messages.error(request, err)
        if err == REGISTER_PAY_CLUB_ENTRY_FEE_MSG:
            return _build_tournament_payment_redirect(
                tournament,
                next_url=request.build_absolute_uri(
                    reverse(
                        "tournament_register_doubles", kwargs={"slug": tournament.slug}
                    )
                ),
            )
        if err == REGISTER_PAY_OR_SUBSCRIBE_MSG:
            return redirect("tournament_register_required", slug=tournament.slug)
        return redirect("tournament_detail", slug=tournament.slug)

    # Проверка пола для микст-турниров
    if tournament.is_mixed_doubles():
        if not player.gender or not team.player1.gender:
            messages.error(
                request, "Для участия в микст-турнире необходимо указать пол в профиле."
            )
            return redirect("tournament_detail", slug=tournament.slug)
        if player.gender == team.player1.gender:
            gender_text = "женщину" if player.gender == "male" else "мужчину"
            messages.error(
                request,
                f"Это микст-турнир. В команде должны быть мужчина и женщина. Выберите партнёра противоположного пола ({gender_text}).",
            )
            return redirect("tournament_detail", slug=tournament.slug)

    tournament_member = _get_tournament_club_member(request.user, tournament)
    if tournament_member:
        consumed, consume_error = consume_member_tournament_limit(
            tournament_member,
            tournament,
        )
        if not consumed:
            messages.error(request, consume_error)
            return redirect("tournament_detail", slug=tournament.slug)
        mark_registration_covered(
            tournament,
            request.user,
            TournamentRegistrationCoverage.CoverageType.CLUB_PLAN_SLOT,
        )

    team.player2 = player
    team.save()
    if not _tournament_does_not_consume_subscription_limit(tournament):
        try:
            sub = request.user.subscription
            if sub and sub.spend_fancoin(
                TOURNAMENT_REGISTRATION_COST,
                reason=FancoinTransaction.Reason.TOURNAMENT_REGISTRATION,
                tournament=tournament,
            ):
                mark_registration_covered(
                    tournament,
                    request.user,
                    TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT,
                )
        except Exception:
            pass
    paid_ids = request.session.get("tournament_entry_paid") or []
    if tournament.id in paid_ids:
        paid_ids = [i for i in paid_ids if i != tournament.id]
        request.session["tournament_entry_paid"] = paid_ids
        request.session.modified = True
    Notification.objects.create(
        user=team.player1.user,
        message=f"{player} присоединился к вашей команде в турнире {tournament.name}.",
        url=reverse("tournament_detail", args=[tournament.slug]),
    )
    try:
        from apps.core.telegram_notify import notify_tournament_registration
        from apps.telegram_bot import notifications as tg

        notify_tournament_registration(request.user, tournament)
        tg.notify_tournament_registered(request.user, tournament)
    except Exception:
        pass
    messages.success(
        request, f"Вы присоединились к команде с {team.player1}. Регистрация завершена."
    )
    return redirect("tournament_detail", slug=tournament.slug)


def _do_add_partner(request, tournament, player, partner_id):
    """Создать команду с указанным партнёром."""
    try:
        partner = Player.objects.get(pk=partner_id)
    except Player.DoesNotExist:
        messages.error(request, "Игрок не найден.")
        return redirect("tournament_register_doubles", slug=tournament.slug)

    if tournament.club_id:
        is_active_club_member = ClubMember.objects.filter(
            club=tournament.club,
            user=partner.user,
            status=ClubMemberStatus.ACTIVE,
        ).exists()
        if not is_active_club_member:
            messages.error(
                request,
                "Для клубного турнира можно выбрать партнёра только из активных участников клуба.",
            )
            return redirect("tournament_register_doubles", slug=tournament.slug)

    if partner.id == player.id:
        messages.error(request, "Нельзя добавить себя в пару.")
        return redirect("tournament_register_doubles", slug=tournament.slug)

    if _is_player_registered_in_doubles(tournament, partner):
        messages.error(request, f"{partner} уже зарегистрирован в этом турнире.")
        return redirect("tournament_register_doubles", slug=tournament.slug)

    # Проверка пола для микст-турниров
    if tournament.is_mixed_doubles():
        if not player.gender or not partner.gender:
            messages.error(
                request,
                "Для участия в микст-турнире необходимо указать пол в профиле (у вас и у партнёра).",
            )
            return redirect("tournament_register_doubles", slug=tournament.slug)
        if player.gender == partner.gender:
            gender_text = "женщину" if player.gender == "male" else "мужчину"
            messages.error(
                request,
                f"Это микст-турнир. В команде должны быть мужчина и женщина. Выберите партнёра противоположного пола ({gender_text}).",
            )
            return redirect("tournament_register_doubles", slug=tournament.slug)

    # Проверка всех условий регистрации для текущего игрока (включая категории)
    ok, err = _check_tournament_registration_eligibility(request, tournament, player)
    if not ok:
        messages.error(request, err)
        if err == REGISTER_PAY_CLUB_ENTRY_FEE_MSG:
            return _build_tournament_payment_redirect(
                tournament,
                next_url=request.build_absolute_uri(
                    reverse(
                        "tournament_register_doubles", kwargs={"slug": tournament.slug}
                    )
                ),
            )
        if err == REGISTER_PAY_OR_SUBSCRIBE_MSG:
            return redirect("tournament_register_required", slug=tournament.slug)
        return redirect("tournament_detail", slug=tournament.slug)

    partner_ok, partner_err = _check_user_can_register_for_tournament(
        partner.user, tournament
    )
    if not partner_ok:
        messages.error(request, f"Партнёр не может участвовать: {partner_err}")
        return redirect("tournament_register_doubles", slug=tournament.slug)

    tournament_member = _get_tournament_club_member(request.user, tournament)
    if tournament_member:
        consumed, consume_error = consume_member_tournament_limit(
            tournament_member,
            tournament,
        )
        if not consumed:
            messages.error(request, consume_error)
            return redirect("tournament_detail", slug=tournament.slug)
        mark_registration_covered(
            tournament,
            request.user,
            TournamentRegistrationCoverage.CoverageType.CLUB_PLAN_SLOT,
        )

    partner_tournament_member = _get_tournament_club_member(partner.user, tournament)
    if partner_tournament_member:
        partner_consumed, partner_consume_error = consume_member_tournament_limit(
            partner_tournament_member,
            tournament,
        )
        if not partner_consumed:
            messages.error(
                request, f"Партнёр не может участвовать: {partner_consume_error}"
            )
            return redirect("tournament_register_doubles", slug=tournament.slug)
        mark_registration_covered(
            tournament,
            partner.user,
            TournamentRegistrationCoverage.CoverageType.CLUB_PLAN_SLOT,
        )

    TournamentTeam.objects.create(
        tournament=tournament, player1=player, player2=partner
    )
    if not _tournament_does_not_consume_subscription_limit(tournament):
        try:
            sub = request.user.subscription
            if sub and sub.spend_fancoin(
                TOURNAMENT_REGISTRATION_COST,
                reason=FancoinTransaction.Reason.TOURNAMENT_REGISTRATION,
                tournament=tournament,
            ):
                mark_registration_covered(
                    tournament,
                    request.user,
                    TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT,
                )
        except Exception:
            pass
        try:
            psub = partner.user.subscription
            if psub and psub.spend_fancoin(
                TOURNAMENT_REGISTRATION_COST,
                reason=FancoinTransaction.Reason.TOURNAMENT_REGISTRATION,
                tournament=tournament,
            ):
                mark_registration_covered(
                    tournament,
                    partner.user,
                    TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT,
                )
        except Exception:
            pass
    paid_ids = request.session.get("tournament_entry_paid") or []
    if tournament.id in paid_ids:
        paid_ids = [i for i in paid_ids if i != tournament.id]
        request.session["tournament_entry_paid"] = paid_ids
        request.session.modified = True
    Notification.objects.create(
        user=partner.user,
        message=f"{player} добавил вас в команду на турнир {tournament.name}.",
        url=reverse("tournament_detail", args=[tournament.slug]),
    )
    try:
        from apps.core.telegram_notify import notify_tournament_registration
        from apps.telegram_bot import notifications as tg

        notify_tournament_registration(request.user, tournament)
        notify_tournament_registration(partner.user, tournament)
        tg.notify_tournament_registered(request.user, tournament)
        tg.notify_tournament_registered(partner.user, tournament)
    except Exception:
        pass
    messages.success(request, f"Команда зарегистрирована: вы и {partner}.")
    return redirect("tournament_detail", slug=tournament.slug)


@login_required
@require_filled_profile
@require_verified_player
def tournament_join_team(request, slug, team_id):
    """Присоединиться к команде (партнёр без пары)."""
    if request.method != "POST":
        return redirect("tournament_detail", slug=slug)
    tournament = get_object_or_404(Tournament, slug=slug)
    team = get_object_or_404(
        TournamentTeam, tournament=tournament, pk=team_id, player2__isnull=True
    )
    player = getattr(request.user, "player", None)
    if player is None:
        player = Player.objects.create(user=request.user)
    return _do_join_team(request, tournament, player, team)
