import csv
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, QuerySet
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.tournaments.models import Match

from ..models import Club, ClubMember, ClubMemberStatus, ClubRating, ClubRatingHistory
from .helpers import (
    _build_club_profile_context,
    _get_club_and_check_manage,
    _get_current_club_member,
    _resolve_club_manage,
)


def _build_last_club_match_map(
    club: Club,
    player_ids: set[int],
) -> dict[int, Match]:
    """Возвращает последний сыгранный матч клуба для каждого игрока."""
    if not player_ids:
        return {}

    matches = (
        Match.objects.filter(tournament__club=club)
        .filter(status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER])
        .filter(
            Q(player1_id__in=player_ids)
            | Q(player2_id__in=player_ids)
            | Q(team1__player1_id__in=player_ids)
            | Q(team1__player2_id__in=player_ids)
            | Q(team2__player1_id__in=player_ids)
            | Q(team2__player2_id__in=player_ids)
        )
        .select_related(
            "tournament",
            "player1",
            "player2",
            "team1",
            "team2",
            "partner1",
            "partner2",
            "winner",
            "winner_team",
        )
        .annotate(
            effective_date=Coalesce(
                "completed_datetime", "scheduled_datetime", "deadline"
            ),
        )
        .order_by("-effective_date", "-pk")
        .distinct()
    )

    last_match_by_player: dict[int, Match] = {}
    for match in matches:
        participant_ids = {
            match.player1_id,
            match.player2_id,
            getattr(match.team1, "player1_id", None),
            getattr(match.team1, "player2_id", None),
            getattr(match.team2, "player1_id", None),
            getattr(match.team2, "player2_id", None),
            match.partner1_id,
            match.partner2_id,
        }
        for player_id in participant_ids:
            if (
                player_id
                and player_id in player_ids
                and player_id not in last_match_by_player
            ):
                last_match_by_player[player_id] = match
        if len(last_match_by_player) == len(player_ids):
            break

    return last_match_by_player


def _enrich_club_ratings(
    club: Club,
    ratings_qs: QuerySet[ClubRating],
) -> list[ClubRating]:
    """Добавляет отображаемый ранг и последний матч клуба к строкам рейтинга."""
    ratings = list(
        ratings_qs.select_related(
            "member", "member__user", "member__user__player"
        ).order_by("-points")
    )
    player_ids = set()
    for rating in ratings:
        player = getattr(rating.member.user, "player", None)
        if player is not None:
            player_ids.add(player.pk)
    last_match_by_player = _build_last_club_match_map(club, player_ids)

    for index, rating in enumerate(ratings, 1):
        rating.display_rank = index
        player = getattr(rating.member.user, "player", None)
        rating.last_club_match = None
        if player is None:
            continue
        match = last_match_by_player.get(player.pk)
        if match is None:
            continue
        on_side1 = bool(
            match.player1_id == player.pk
            or (
                match.team1
                and (
                    match.team1.player1_id == player.pk
                    or match.team1.player2_id == player.pk
                )
            )
            or match.partner1_id == player.pk
        )
        opponent = (
            match.get_player2_display() if on_side1 else match.get_player1_display()
        )
        rating.last_club_match = {
            "date": getattr(match, "effective_date", None),
            "opponent": opponent,
            "tournament_name": match.tournament.name if match.tournament_id else "",
            "score": match.score_display,
            "url": (
                reverse("tournament_detail", kwargs={"slug": match.tournament.slug})
                if match.tournament_id
                else ""
            ),
        }

    return ratings


@login_required
@require_GET
def player_profile(request: HttpRequest, slug: str, player_id: int) -> HttpResponse:
    """Клубный профиль участника, доступный любому активному члену клуба."""
    viewer_member = (
        ClubMember.objects.filter(
            user=request.user,
            club__slug=slug,
            status=ClubMemberStatus.ACTIVE,
        )
        .select_related("club")
        .first()
    )
    if viewer_member is None:
        messages.error(
            request, "Профиль игрока в клубе доступен только участникам клуба."
        )
        return redirect("clubs:club_public_detail", slug=slug)

    club = viewer_member.club
    member = get_object_or_404(
        ClubMember.objects.select_related("user", "user__player"),
        club=club,
        user__player__pk=player_id,
        status=ClubMemberStatus.ACTIVE,
    )
    player = getattr(member.user, "player", None)
    if player is None:
        messages.error(request, "У этого участника нет профиля игрока.")
        return redirect("clubs:club_public_detail", slug=slug)

    context = _build_club_profile_context(
        request,
        club=club,
        member=member,
        player=player,
        is_profile_owner=request.user.id == member.user_id,
    )
    context["club_profile_url"] = reverse(
        "clubs:player_profile",
        kwargs={"slug": club.slug, "player_id": player.pk},
    )
    return render(request, "users/profile.html", context)


@login_required
@require_GET
def club_rating(request: HttpRequest) -> HttpResponse:
    """Раздел «Рейтинг клуба» — место, очки, таблица, история."""
    member = _get_current_club_member(request)
    if not member:
        return redirect("clubs:register_choice")

    club = member.club
    ratings = _enrich_club_ratings(club, ClubRating.objects.filter(club=club))
    my_rating = next((r for r in ratings if r.member_id == member.id), None)
    history: list[Any] = []
    if my_rating:
        history = list(
            ClubRatingHistory.objects.filter(club_rating=my_rating)
            .select_related("tournament")
            .order_by("-created_at")[:20]
        )

    return render(
        request,
        "clubs/club_rating.html",
        {
            "club": club,
            "is_club_panel": True,
            "ratings": ratings,
            "my_rating": my_rating,
            "history": history,
        },
    )


@login_required
@require_GET
def dashboard_rating(request: HttpRequest, slug: str) -> HttpResponse:
    """Таблица рейтинга клуба (панель админа/менеджера)."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    ratings = _enrich_club_ratings(club, ClubRating.objects.filter(club=club))
    return render(
        request,
        "clubs/dashboard_rating.html",
        {"club": club, "ratings": ratings, "is_club_panel": True},
    )


@login_required
@require_GET
def dashboard_rating_export(request: HttpRequest, slug: str) -> HttpResponse:
    """Экспорт рейтинга клуба в CSV."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    ratings = list(
        ClubRating.objects.filter(club=club)
        .select_related("member", "member__user")
        .order_by("-points")
    )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="rating_{club.slug}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["place", "email", "name", "points"])
    for index, rating in enumerate(ratings, 1):
        name = (
            rating.member.user.get_full_name() or rating.member.user.email or ""
        ).strip()
        writer.writerow([index, rating.member.user.email, name, rating.points])
    return response
