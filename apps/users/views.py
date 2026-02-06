"""
Users views.
"""

import json
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.models import UserTelegramLink
from .forms import PlayerProfileForm, UserRegistrationForm
from .models import Notification, Player, SkillLevel, User


def _map_ntrp_to_skill_level(level: Decimal) -> str:
    normalized = int(level.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if normalized <= 2:
        return SkillLevel.NOVICE
    if normalized <= 4:
        return SkillLevel.AMATEUR
    if normalized == 5:
        return SkillLevel.EXPERIENCED
    if normalized == 6:
        return SkillLevel.ADVANCED
    return SkillLevel.PROFESSIONAL


def _get_profile_progress_data(player: Player) -> list[dict[str, Any]]:
    """
    Build time series for profile charts: from registration to today,
    cumulative points, matches count, win rate %.
    Returns list of {"date": "YYYY-MM-DD", "points": int, "matches": int, "win_rate": float}.
    """
    from apps.tournaments.models import Match, TournamentPlayerResult

    events: list[tuple[date, int, int, int]] = []  # (date, points_delta, matches_delta, wins_delta)

    # Completed matches (singles: player1/player2; doubles: team1/team2)
    match_qs = (
        Match.objects.filter(
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
        .distinct()
        .select_related("tournament", "winner", "winner_team", "player1", "player2", "team1", "team2")
        .order_by("completed_datetime", "scheduled_datetime", "pk")
    )

    for m in match_qs:
        event_date = (
            (m.completed_datetime and m.completed_datetime.date())
            or (m.scheduled_datetime and m.scheduled_datetime.date())
            or timezone.now().date()
        )
        # Points for this player in this match (non-FAN/round_robin store in match)
        if m.team1_id and m.team2_id:
            on_team1 = (m.team1 and (m.team1.player1_id == player.pk or m.team1.player2_id == player.pk))
            pts = m.points_player1 if on_team1 else m.points_player2
        else:
            pts = m.points_player1 if m.player1_id == player.pk else m.points_player2
        won = bool(
            (m.winner_id == player.pk)
            or (m.winner_team_id and m.team1_id and m.winner_team_id == m.team1_id and (m.team1.player1_id == player.pk or m.team1.player2_id == player.pk))
            or (m.winner_team_id and m.team2_id and m.winner_team_id == m.team2_id and (m.team2.player1_id == player.pk or m.team2.player2_id == player.pk))
        )
        events.append((event_date, pts or 0, 1, 1 if won else 0))

    # FAN tournament results (points awarded at tournament end)
    fan_results = (
        TournamentPlayerResult.objects.filter(player=player)
        .select_related("tournament")
        .order_by("tournament__end_date", "tournament__pk")
    )
    for r in fan_results:
        event_date = (r.tournament.end_date or r.tournament.start_date or timezone.now().date())
        events.append((event_date, r.fan_points, 0, 0))

    events.sort(key=lambda x: (x[0], x[1], x[2], x[3]))

    # Cumulative series from registration
    start = player.created_at.date() if player.created_at else timezone.now().date()
    result = [{"date": start.isoformat(), "points": 0, "matches": 0, "win_rate": 0.0}]
    cum_pts = 0
    cum_matches = 0
    cum_wins = 0

    for event_date, d_pts, d_m, d_w in events:
        cum_pts += d_pts
        cum_matches += d_m
        cum_wins += d_w
        wr = round(cum_wins / cum_matches * 100, 1) if cum_matches else 0.0
        result.append({
            "date": event_date.isoformat(),
            "points": cum_pts,
            "matches": cum_matches,
            "win_rate": wr,
        })

    today = timezone.now().date()
    if result and result[-1]["date"] != today.isoformat() and (cum_matches > 0 or cum_pts != 0):
        result.append({
            "date": today.isoformat(),
            "points": player.total_points,
            "matches": player.matches_played,
            "win_rate": float(player.win_rate),
        })

    # Ensure series matches current totals: fix last point and scale if cumulative was wrong
    if result:
        last_pts = result[-1]["points"]
        last_matches = result[-1]["matches"]
        if last_pts > 0 and last_pts != player.total_points:
            ratio_pts = player.total_points / last_pts
            for r in result:
                r["points"] = int(round(r["points"] * ratio_pts))
        else:
            result[-1]["points"] = player.total_points
        if last_matches > 0 and last_matches != player.matches_played:
            ratio_m = player.matches_played / last_matches
            for r in result:
                r["matches"] = int(round(r["matches"] * ratio_m))
        else:
            result[-1]["matches"] = player.matches_played
        result[-1]["win_rate"] = round(float(player.win_rate), 1)

    return result


def auth(request):
    """Объединённая страница регистрации и входа с анимацией переключения."""
    # Определяем активный режим из GET параметра или по умолчанию register
    active_mode = request.GET.get('mode', 'register')
    if active_mode not in ('register', 'login'):
        active_mode = 'register'
    
    register_form = None
    login_form = None
    
    if request.method == "POST":
        # Проверяем какая форма была отправлена по наличию полей
        if 'email' in request.POST and 'first_name' in request.POST:
            # Форма регистрации
            active_mode = 'register'
            register_form = UserRegistrationForm(request.POST)
            if register_form.is_valid():
                user = register_form.save()
                ntrp = register_form.cleaned_data["ntrp_level"]
                level_decimal = Decimal(ntrp)
                skill = _map_ntrp_to_skill_level(level_decimal)
                player = Player.objects.create(
                    user=user,
                    birth_date=register_form.cleaned_data["birth_date"],
                    city=register_form.cleaned_data["city"].strip(),
                    ntrp_level=level_decimal,
                    skill_level=skill,
                )
                from apps.core.telegram_notify import notify_new_registration
                notify_new_registration(user, player)
                login(request, user)
                messages.success(request, "Регистрация успешна! Добро пожаловать.")
                return redirect("home")
        elif 'username' in request.POST and 'password' in request.POST:
            # Форма входа
            active_mode = 'login'
            login_form = AuthenticationForm(request, data=request.POST)
            if login_form.is_valid():
                user = login_form.get_user()
                login(request, user)
                messages.success(request, f"Добро пожаловать, {user.get_full_name() or user.email}!")
                return redirect("home")
    
    # Инициализируем формы если они не были созданы выше
    if register_form is None:
        register_form = UserRegistrationForm()
    if login_form is None:
        login_form = AuthenticationForm(request)
    
    return render(request, "users/auth.html", {
        "register_form": register_form,
        "login_form": login_form,
        "active_mode": active_mode,
    })


def register(request):
    """Редирект на объединённую страницу авторизации."""
    return redirect(reverse("auth") + "?mode=register")


def login_view(request):
    """Редирект на объединённую страницу авторизации."""
    return redirect(reverse("auth") + "?mode=login")


def profile(request, pk):
    """User profile view."""
    player = get_object_or_404(
        Player.objects.select_related(
            'user', 'user__subscription', 'user__subscription__tier'
        ),
        pk=pk,
    )

    from apps.tournaments.models import Match

    recent_matches = Match.objects.filter(
        Q(player1=player)
        | Q(player2=player)
        | Q(team1__player1=player)
        | Q(team1__player2=player)
        | Q(team2__player1=player)
        | Q(team2__player2=player)
    ).select_related(
        "tournament", "player1", "player2", "winner", "team1", "team2", "winner_team"
    ).order_by("-scheduled_datetime")[:10]

    progress_data = _get_profile_progress_data(player)

    subscription_usage_percent = 0
    if getattr(player.user, "subscription", None):
        sub = player.user.subscription
        tier = getattr(sub, "tier", None)
        max_t = getattr(tier, "max_tournaments", None) if tier else None
        if max_t and max_t > 0:
            subscription_usage_percent = min(
                100,
                int(100 * sub.tournaments_registered_count / max_t),
            )

    telegram_user_bot_connected = False
    telegram_bot_username = ""
    if request.user.is_authenticated and request.user == player.user:
        try:
            link = request.user.telegram_link
            telegram_user_bot_connected = link.user_bot_chat_id is not None
        except UserTelegramLink.DoesNotExist:
            pass
        if telegram_user_bot_connected:
            try:
                from apps.telegram_bot import services as bot_services
                telegram_bot_username = bot_services.get_bot_username() or ""
            except Exception:
                pass

    context = {
        "player": player,
        "recent_matches": recent_matches,
        "profile_progress_data": progress_data,
        "subscription_usage_percent": subscription_usage_percent,
        "telegram_user_bot_connected": telegram_user_bot_connected,
        "telegram_bot_username": telegram_bot_username,
    }
    return render(request, "users/profile.html", context)


@login_required
def profile_edit(request):
    """Edit profile view."""
    try:
        player = request.user.player
    except Player.DoesNotExist:
        player = Player.objects.create(user=request.user)

    if request.method == 'POST':
        form = PlayerProfileForm(request.POST, request.FILES, instance=player, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Профиль обновлён.')
            return redirect('profile', pk=player.pk)
    else:
        form = PlayerProfileForm(instance=player, user=request.user)

    return render(
        request,
        "users/profile_edit.html",
        {"form": form},
    )


@login_required
def notifications(request):
    """User notifications inbox."""

    notes = Notification.objects.filter(user=request.user).order_by('-created_at')
    # mark all as read when viewed
    notes.filter(is_read=False).update(is_read=True)
    return render(request, 'users/notifications.html', {'notifications': notes})


def ntrp_test(request):
    """Public NTRP test page."""
    can_save = request.user.is_authenticated
    return render(request, 'users/ntrp_test.html', {'can_save': can_save})


@login_required
@require_POST
def save_ntrp(request):
    """Save NTRP level for the authenticated user's player profile."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    raw_level = payload.get("ntrp_level")
    if raw_level is None:
        return JsonResponse({"ok": False, "error": "missing_level"}, status=400)

    try:
        level = Decimal(str(raw_level))
    except (InvalidOperation, ValueError):
        return JsonResponse({"ok": False, "error": "invalid_level"}, status=400)

    if level < Decimal("1.0") or level > Decimal("7.0"):
        return JsonResponse({"ok": False, "error": "out_of_range"}, status=400)

    try:
        player = request.user.player
    except Player.DoesNotExist:
        return JsonResponse({"ok": False, "error": "player_not_found"}, status=404)

    player.ntrp_level = level
    player.skill_level = _map_ntrp_to_skill_level(level)
    player.save(update_fields=["ntrp_level", "skill_level"])
    return JsonResponse({"ok": True, "ntrp_level": f"{level:.1f}"})
