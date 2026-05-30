import logging
from typing import Any, cast

from django.contrib import messages
from django.db.models import Max, Min, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from apps.tournaments.models import Match, TournamentPlayerResult, TournamentStatus

from ..models import Club, ClubMember, ClubMembershipFee, ClubMemberStatus
from ..plan_services import get_member_active_plan, get_member_plan_limits
from ..services import (
    club_is_operational,
    get_club_current_subscription,
    get_fee_expiring_soon_text,
    get_fee_status_for_member,
    user_can_manage_club,
)

logger = logging.getLogger(__name__)


def _remember_current_club(request: HttpRequest, club: Club) -> None:
    """Синхронизирует текущий клуб в сессии с открытым клубным разделом."""
    current_slug = str(request.session.get("current_club_slug") or "").strip()
    if current_slug == club.slug:
        return
    request.session["current_club_slug"] = club.slug


def _get_current_club_member(request: HttpRequest) -> ClubMember | None:
    """Возвращает ClubMember для текущего клуба пользователя из сессии или None."""
    if not request.user.is_authenticated:
        return None
    slug = request.session.get("current_club_slug")
    if not slug:
        first = (
            ClubMember.objects.filter(
                user=request.user,
                status=ClubMemberStatus.ACTIVE,
            )
            .select_related("club")
            .order_by("club__name")
            .first()
        )
        member = cast(ClubMember | None, first)
        if member:
            request.session["current_club_slug"] = member.club.slug
        return member
    existing = (
        ClubMember.objects.filter(
            user=request.user,
            club__slug=slug,
            status=ClubMemberStatus.ACTIVE,
        )
        .select_related("club")
        .first()
    )
    return cast(ClubMember | None, existing)


def _get_club_payment_settings(club: Club) -> ClubMembershipFee | None:
    """Возвращает запись с настройками платёжного провайдера клуба."""
    payment_settings = (
        ClubMembershipFee.objects.filter(
            club=club,
            payment_provider=ClubMembershipFee.PaymentProvider.YOOKASSA,
        )
        .exclude(payment_shop_id="")
        .exclude(payment_api_key="")
        .order_by("-id")
        .first()
    )
    return cast(ClubMembershipFee | None, payment_settings)


def _build_club_profile_context(
    request: HttpRequest,
    *,
    club: Club,
    member: ClubMember,
    player: Any,
    is_profile_owner: bool,
) -> dict[str, Any]:
    """Собирает контекст клубного кабинета игрока."""
    from apps.users.rating_utils import rating_to_ntrp_level

    fee = (
        ClubMembershipFee.objects.filter(club=club, is_active=True)
        .order_by("-id")
        .first()
    )
    fee_status = get_fee_status_for_member(club, member) if fee else None
    fee_expiring_text = (
        get_fee_expiring_soon_text(fee) if fee and fee_status == "expiring_soon" else ""
    )
    member_plan = get_member_active_plan(member)
    plan_limits = get_member_plan_limits(member)
    club_subscription = get_club_current_subscription(club)

    all_matches_qs = (
        Match.objects.filter(tournament__club=club)
        .filter(
            Q(player1=player)
            | Q(player2=player)
            | Q(team1__player1=player)
            | Q(team1__player2=player)
            | Q(team2__player1=player)
            | Q(team2__player2=player)
        )
        .select_related(
            "tournament",
            "tournament__club",
            "player1",
            "player2",
            "winner",
            "team1",
            "team2",
            "winner_team",
        )
        .distinct()
    )
    from apps.tournaments.utils import order_player_matches_for_display

    all_matches_qs = order_player_matches_for_display(all_matches_qs)

    filter_year = request.GET.get("year")
    filter_month = request.GET.get("month")
    filter_status = request.GET.get("status")

    date_range = all_matches_qs.aggregate(
        min_date=Min("effective_date"),
        max_date=Max("effective_date"),
    )
    min_date = date_range["min_date"]
    max_date = date_range["max_date"]

    available_years: list[int] = []
    if min_date and max_date:
        available_years = list(range(max_date.year, min_date.year - 1, -1))

    active_year: int | None = None
    active_month: int | None = None
    active_status: str | None = None

    if filter_year:
        try:
            active_year = int(filter_year)
        except (ValueError, TypeError):
            active_year = None

    if filter_month:
        try:
            active_month = int(filter_month)
            if not (1 <= active_month <= 12):
                active_month = None
        except (ValueError, TypeError):
            active_month = None

    if filter_status:
        valid_statuses = [status[0] for status in Match.MatchStatus.choices]
        if filter_status in valid_statuses:
            active_status = filter_status

    if active_year:
        all_matches_qs = all_matches_qs.filter(effective_date__year=active_year)
    if active_month and active_year:
        all_matches_qs = all_matches_qs.filter(effective_date__month=active_month)
    if active_status:
        all_matches_qs = all_matches_qs.filter(status=active_status)

    recent_matches = list(all_matches_qs)

    months_ru = [
        (1, "Январь"),
        (2, "Февраль"),
        (3, "Март"),
        (4, "Апрель"),
        (5, "Май"),
        (6, "Июнь"),
        (7, "Июль"),
        (8, "Август"),
        (9, "Сентябрь"),
        (10, "Октябрь"),
        (11, "Ноябрь"),
        (12, "Декабрь"),
    ]

    def _build_club_profile_progress_data() -> list[dict[str, Any]]:
        completed_qs = (
            Match.objects.filter(tournament__club=club)
            .filter(
                Q(player1=player)
                | Q(player2=player)
                | Q(team1__player1=player)
                | Q(team1__player2=player)
                | Q(team2__player1=player)
                | Q(team2__player2=player)
            )
            .filter(
                status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]
            )
            .select_related("winner", "winner_team", "team1", "team2")
            .order_by("completed_datetime", "scheduled_datetime", "pk")
            .distinct()
        )

        start = player.created_at.date() if player.created_at else timezone.now().date()
        result: list[dict[str, Any]] = [
            {
                "date": start.isoformat(),
                "points": 0.0,
                "matches": 0,
                "win_rate": 0.0,
                "won": None,
                "fan_delta": 0.0,
                "ntrp_before": 0.0,
                "ntrp_after": 0.0,
            }
        ]

        rating_points = 0.0
        cum_matches = 0
        cum_wins = 0

        for match in completed_qs:
            event_date = (
                (match.completed_datetime and match.completed_datetime.date())
                or (match.scheduled_datetime and match.scheduled_datetime.date())
                or timezone.now().date()
            )
            if match.team1_id and match.team2_id:
                on_team1 = match.team1 and (
                    match.team1.player1_id == player.pk
                    or match.team1.player2_id == player.pk
                )
                fan_delta = float(
                    match.rating_delta_player1
                    if on_team1
                    else match.rating_delta_player2
                )
            else:
                fan_delta = float(
                    match.rating_delta_player1
                    if match.player1_id == player.pk
                    else match.rating_delta_player2
                )

            won = bool(
                (match.winner_id == player.pk)
                or (
                    match.winner_team_id
                    and match.team1_id
                    and match.winner_team_id == match.team1_id
                    and (
                        match.team1.player1_id == player.pk
                        or match.team1.player2_id == player.pk
                    )
                )
                or (
                    match.winner_team_id
                    and match.team2_id
                    and match.winner_team_id == match.team2_id
                    and (
                        match.team2.player1_id == player.pk
                        or match.team2.player2_id == player.pk
                    )
                )
            )

            rating_before = rating_points
            rating_points += fan_delta
            ntrp_before_val = rating_to_ntrp_level(rating_before)
            ntrp_after_val = rating_to_ntrp_level(rating_points)
            ntrp_before = float(ntrp_before_val) if ntrp_before_val else 0.0
            ntrp_after = float(ntrp_after_val) if ntrp_after_val else 0.0

            cum_matches += 1
            if won:
                cum_wins += 1
            win_rate = round(cum_wins / cum_matches * 100, 1) if cum_matches else 0.0

            on_team1 = (
                match.team1_id
                and (
                    match.team1.player1_id == player.pk
                    or match.team1.player2_id == player.pk
                )
            ) or (match.player1_id == player.pk)
            opponent = (
                match.get_player2_display() if on_team1 else match.get_player1_display()
            )

            result.append(
                {
                    "date": event_date.isoformat(),
                    "points": round(rating_points, 1),
                    "matches": cum_matches,
                    "win_rate": win_rate,
                    "won": won,
                    "fan_delta": fan_delta,
                    "ntrp_before": ntrp_before,
                    "ntrp_after": ntrp_after,
                    "match_id": match.pk,
                    "match_opponent": opponent,
                    "match_score": match.score_display,
                }
            )

        return result

    def _build_club_season_points_data() -> list[dict[str, Any]]:
        points_series: list[dict[str, Any]] = []
        cumulative_points = 0
        fan_results = (
            TournamentPlayerResult.objects.filter(
                player=player,
                tournament__club=club,
                tournament__status=TournamentStatus.COMPLETED,
            )
            .select_related("tournament")
            .order_by(
                "tournament__end_date",
                "tournament__start_date",
                "tournament__pk",
                "pk",
            )
        )
        for result_item in fan_results:
            tournament = result_item.tournament
            event_date = (
                tournament.end_date or tournament.start_date or timezone.now().date()
            )
            cumulative_points += int(result_item.fan_points or 0)
            points_series.append(
                {
                    "date": event_date.isoformat(),
                    "season_points": cumulative_points,
                }
            )
        if not points_series:
            today = timezone.now().date().isoformat()
            return [{"date": today, "season_points": 0}]
        return points_series

    progress_data = _build_club_profile_progress_data()
    season_points_data = _build_club_season_points_data()
    season_points_current = (
        season_points_data[-1]["season_points"] if season_points_data else 0
    )

    from types import SimpleNamespace

    season_points = SimpleNamespace(current_season_points=season_points_current)
    current_season_display = f"Клуб {club.name}"

    matches_played = sum(1 for row in progress_data if row.get("matches", 0) > 0)
    wins = sum(
        1
        for row in progress_data
        if row.get("matches", 0) > 0 and row.get("won") is True
    )
    club_win_rate = round((wins / matches_played) * 100, 1) if matches_played else 0.0
    club_points_now = (
        float(progress_data[-1].get("points", 0.0)) if progress_data else 0.0
    )

    if (
        progress_data
        and progress_data[-1]["date"] != timezone.now().date().isoformat()
        and matches_played > 0
    ):
        progress_data.append(
            {
                "date": timezone.now().date().isoformat(),
                "points": club_points_now,
                "matches": matches_played,
                "win_rate": club_win_rate,
                "won": None,
                "fan_delta": 0.0,
                "ntrp_before": 0.0,
                "ntrp_after": float(rating_to_ntrp_level(club_points_now) or 0.0),
            }
        )

    subscription_usage_percent = 0
    if plan_limits and plan_limits.monthly_tournaments_limit:
        if plan_limits.monthly_tournaments_limit > 0:
            used = plan_limits.tournaments_used
            limit = plan_limits.monthly_tournaments_limit
            subscription_usage_percent = min(100, int((used / limit) * 100))

    from apps.payments.models import SavedPaymentMethod

    club_plan_autopay_card = None
    club_fee_autopay_card = None
    if is_profile_owner:
        club_plan_autopay_card = (
            SavedPaymentMethod.objects.filter(
                user=player.user,
                club=club,
                is_active=True,
                is_default_for_club_plans=True,
            )
            .order_by("-created_at")
            .first()
        )
        club_fee_autopay_card = (
            SavedPaymentMethod.objects.filter(
                user=player.user,
                club=club,
                is_active=True,
                is_default_for_club_fees=True,
            )
            .order_by("-created_at")
            .first()
        )

    from apps.player_ratings.services import get_player_skills

    player_skills_data = None
    try:
        player_skills_data = get_player_skills(
            player,
            request.user,
            include_lowest_three=True,
        )
    except Exception:
        player_skills_data = None

    return {
        "club": club,
        "is_club_panel": True,
        "is_club_profile": True,
        "player": player,
        "member": member,
        "member_plan": member_plan,
        "club_subscription": club_subscription,
        "plan_limits": plan_limits,
        "fee": fee,
        "fee_status": fee_status,
        "fee_expiring_text": fee_expiring_text,
        "recent_matches": recent_matches,
        "profile_progress_data": progress_data,
        "subscription_usage_percent": subscription_usage_percent,
        "available_years": available_years,
        "months_ru": months_ru,
        "active_year": active_year,
        "active_month": active_month,
        "active_status": active_status,
        "match_statuses": Match.MatchStatus.choices,
        "season_points": season_points,
        "current_season_display": current_season_display,
        "season_points_data": season_points_data,
        "season_championships": [],
        "player_skills_data": player_skills_data,
        "is_profile_owner": is_profile_owner,
        "can_view_profile_stats": True,
        "subscription_autopay_card": None,
        "club_plan_autopay_card": club_plan_autopay_card,
        "club_fee_autopay_card": club_fee_autopay_card,
        "club_matches_played": matches_played,
        "club_win_rate": club_win_rate,
        "club_points_now": club_points_now,
    }


def _get_club_and_check_manage(
    request: HttpRequest,
    slug: str,
) -> tuple[Club, None] | tuple[None, HttpResponse]:
    """Возвращает клуб или redirect-ответ, если управление недоступно.

    Для сужения типов в представлениях используйте ``_resolve_club_manage``.

    Args:
        request: HTTP-запрос.
        slug: Слаг клуба в URL.

    Returns:
        Либо ``(club, None)`` при успехе, либо ``(None, HttpResponse)`` с редиректом.
    """
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_club(request.user, club):
        messages.error(request, "Нет доступа к управлению клубом.")
        return None, redirect("clubs:club_public_detail", slug=slug)
    if not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return None, redirect("clubs:club_public_detail", slug=slug)
    _remember_current_club(request, club)
    return club, None


def _resolve_club_manage(
    result: tuple[Club, None] | tuple[None, HttpResponse],
) -> Club | HttpResponse:
    """Возвращает клуб после успешной проверки прав или HTTP-редирект."""
    club, redir = result
    if redir is not None:
        return redir
    assert club is not None
    return club
