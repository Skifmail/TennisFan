"""
Users views.
"""

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.decorators import login_required_with_message
from apps.core.models import UserTelegramLink

from .forms import PlayerProfileForm, UserRegistrationForm
from .models import Notification, Player


def _map_ntrp_to_skill_level(level: Decimal) -> str:
    """Map strength level (1.0-7.0) to SkillLevel category (delegates to rating_utils)."""
    from .rating_utils import map_ntrp_to_skill_level

    return map_ntrp_to_skill_level(level)


def _get_profile_progress_data(player: Player) -> list[dict[str, Any]]:
    """
    Build time series for profile charts: from registration to today,
    cumulative points, matches count, win rate %.
    Returns list with match-by-match data including rating changes.
    Each match entry: {"date": "YYYY-MM-DD", "points": int, "matches": int, "win_rate": float,
                       "won": bool, "fan_delta": float, "ntrp_before": float, "ntrp_after": float}.
    """
    from apps.tournaments.models import Match, TournamentPlayerResult
    from apps.users.rating_utils import rating_to_ntrp_level

    # Events: (date, rating_after, matches_delta, wins_delta, won, fan_delta, ntrp_before, ntrp_after, match_id, opponent, score, event_dt)
    events: list[tuple[Any, ...]] = []

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
        .filter(status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER])
        .distinct()
        .select_related(
            "tournament",
            "winner",
            "winner_team",
            "player1",
            "player2",
            "team1",
            "team2",
        )
        .order_by("completed_datetime", "scheduled_datetime", "pk")
    )

    # Текущий рейтинг для расчета изменений
    current_rating = float(player.total_points)

    # Проходим матчи в обратном порядке, чтобы вычислить рейтинг до каждого матча
    matches_list = list(match_qs)
    matches_list.reverse()

    for m in matches_list:
        event_date = (
            (m.completed_datetime and m.completed_datetime.date())
            or (m.scheduled_datetime and m.scheduled_datetime.date())
            or timezone.now().date()
        )
        # Points for this player in this match (non-FAN/round_robin store in match)
        if m.team1_id and m.team2_id:
            on_team1 = m.team1 and (
                m.team1.player1_id == player.pk or m.team1.player2_id == player.pk
            )
            fan_delta = m.rating_delta_player1 if on_team1 else m.rating_delta_player2
        else:
            fan_delta = (
                m.rating_delta_player1
                if m.player1_id == player.pk
                else m.rating_delta_player2
            )

        won = bool(
            (m.winner_id == player.pk)
            or (
                m.winner_team_id
                and m.team1_id
                and m.winner_team_id == m.team1_id
                and (m.team1.player1_id == player.pk or m.team1.player2_id == player.pk)
            )
            or (
                m.winner_team_id
                and m.team2_id
                and m.winner_team_id == m.team2_id
                and (m.team2.player1_id == player.pk or m.team2.player2_id == player.pk)
            )
        )

        # Вычисляем рейтинг до матча (вычитаем дельту из текущего)
        rating_after = current_rating
        rating_before = current_rating - (fan_delta or 0.0)

        # Вычисляем уровень силы до и после матча
        ntrp_before_val = rating_to_ntrp_level(rating_before)
        ntrp_after_val = rating_to_ntrp_level(rating_after)
        ntrp_before = float(ntrp_before_val) if ntrp_before_val else 0.0
        ntrp_after = float(ntrp_after_val) if ntrp_after_val else 0.0

        # Обновляем текущий рейтинг для следующей итерации
        current_rating = rating_before

        # Соперник и счёт для tooltip
        on_team1 = (
            m.team1_id
            and (m.team1.player1_id == player.pk or m.team1.player2_id == player.pk)
        ) or (m.player1_id == player.pk)
        opponent = m.get_player2_display() if on_team1 else m.get_player1_display()
        event_dt = m.completed_datetime or m.scheduled_datetime or timezone.now()
        # Для графика FAN используем фактический рейтинг после матча (points_player1/2 часто 0)
        events.append(
            (
                event_date,
                rating_after,
                1,
                1 if won else 0,
                won,
                fan_delta or 0.0,
                ntrp_before,
                ntrp_after,
                m.pk,
                opponent,
                m.score_display,
                event_dt,
            )
        )

    # Переворачиваем обратно для правильного порядка (от старых к новым)
    events.reverse()

    # FAN tournament results (points awarded at tournament end) - добавляем в конец
    fan_results = (
        TournamentPlayerResult.objects.filter(player=player)
        .select_related("tournament")
        .order_by("tournament__end_date", "tournament__pk")
    )
    for r in fan_results:
        event_date = (
            r.tournament.end_date or r.tournament.start_date or timezone.now().date()
        )
        event_dt = timezone.now()
        if r.tournament.end_date:
            from datetime import datetime

            event_dt = timezone.make_aware(
                datetime.combine(r.tournament.end_date, datetime.min.time())
            )
        elif r.tournament.start_date:
            from datetime import datetime

            event_dt = timezone.make_aware(
                datetime.combine(r.tournament.start_date, datetime.min.time())
            )
        events.append(
            (
                event_date,
                r.fan_points,
                0,
                0,
                None,
                0.0,
                0.0,
                0.0,
                None,
                "",
                "",
                event_dt,
            )
        )

    # Сортировка: по дате, затем по времени (сохраняем хронологический порядок матчей в один день)
    events.sort(key=lambda x: (x[0], x[11] if len(x) > 11 else timezone.now()))

    # Cumulative series from registration (match_id, opponent, score для матчей)
    start = player.created_at.date() if player.created_at else timezone.now().date()
    result = [
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
    cum_pts = 0.0
    cum_matches = 0
    cum_wins = 0

    for ev in events:
        event_date, d_pts, d_m, d_w, won, fan_delta, ntrp_before, ntrp_after = ev[:8]
        match_id = ev[8] if len(ev) > 8 else None
        opponent = ev[9] if len(ev) > 9 else ""
        score = ev[10] if len(ev) > 10 else ""
        cum_matches += d_m
        cum_wins += d_w
        wr = round(cum_wins / cum_matches * 100, 1) if cum_matches else 0.0
        # Для матчей: points = фактический рейтинг (total_points) после матча
        # Для турнирных очков: cum_pts не меняем (это сезонные очки, не FAN-рейтинг)
        if d_m > 0:
            cum_pts = float(d_pts)  # d_pts = rating_after, храним точное значение
        entry = {
            "date": event_date.isoformat(),
            "points": cum_pts,
            "matches": cum_matches,
            "win_rate": wr,
            "won": won if d_m > 0 else None,  # Только для матчей
            "fan_delta": fan_delta if d_m > 0 else 0.0,
            "ntrp_before": ntrp_before if d_m > 0 else 0.0,
            "ntrp_after": ntrp_after if d_m > 0 else 0.0,
        }
        if d_m > 0 and match_id:
            entry["match_id"] = match_id
            entry["match_opponent"] = opponent
            entry["match_score"] = score
        result.append(entry)

    today = timezone.now().date()
    if (
        result
        and result[-1]["date"] != today.isoformat()
        and (cum_matches > 0 or cum_pts != 0)
    ):
        result.append(
            {
                "date": today.isoformat(),
                "points": player.total_points,
                "matches": player.matches_played,
                "win_rate": float(player.win_rate),
                "won": None,
                "fan_delta": 0.0,
                "ntrp_before": 0.0,
                "ntrp_after": float(player.ntrp_level),
            }
        )

    # Ensure series matches current totals: fix last point and scale if cumulative was wrong
    if result:
        last_pts = result[-1]["points"]
        last_matches = result[-1]["matches"]
        if last_pts > 0 and last_pts != player.total_points:
            ratio_pts = player.total_points / last_pts
            for r in result:
                r["points"] = round(float(r["points"]) * ratio_pts, 1)
        else:
            result[-1]["points"] = float(player.total_points)
        if last_matches > 0 and last_matches != player.matches_played:
            ratio_m = player.matches_played / last_matches
            for r in result:
                r["matches"] = int(round(r["matches"] * ratio_m))
        else:
            result[-1]["matches"] = player.matches_played
        result[-1]["win_rate"] = round(float(player.win_rate), 1)
        # Обновляем уровень силы для последней точки
        if result[-1].get("ntrp_after") == 0.0:
            result[-1]["ntrp_after"] = float(player.ntrp_level)

    return result


def auth(request):
    """Объединённая страница регистрации и входа с анимацией переключения."""
    # Определяем активный режим из GET параметра или по умолчанию register
    active_mode = request.GET.get("mode", "register")
    if active_mode not in ("register", "login"):
        active_mode = "register"

    register_form = None
    login_form = None

    if request.method == "POST":
        # Проверяем какая форма была отправлена по наличию полей
        if "email" in request.POST and "city" in request.POST:
            # Форма регистрации
            active_mode = "register"
            register_form = UserRegistrationForm(request.POST)
            if register_form.is_valid():
                from .rating_utils import get_starting_points

                user = register_form.save()
                level_decimal = register_form.cleaned_data["ntrp_level"]
                if not isinstance(level_decimal, Decimal):
                    level_decimal = Decimal(str(level_decimal))
                skill = _map_ntrp_to_skill_level(level_decimal)
                starting_pts = get_starting_points(level_decimal)
                player = Player.objects.create(
                    user=user,
                    # birth_date заполняется позже в профиле
                    city=register_form.cleaned_data["city"].strip(),
                    ntrp_level=level_decimal,
                    skill_level=skill,
                    total_points=starting_pts,
                    hidden_rating=float(starting_pts),
                    is_verified=True,  # Новые пользователи автоматически верифицированы
                )
                from apps.core.telegram_notify import notify_new_registration

                notify_new_registration(user, player)
                login(request, user)
                messages.success(request, "Регистрация успешна! Добро пожаловать.")
                return redirect("home")
        elif "username" in request.POST and "password" in request.POST:
            # Форма входа
            active_mode = "login"
            login_form = AuthenticationForm(request, data=request.POST)
            if login_form.is_valid():
                user = login_form.get_user()
                login(request, user)
                messages.success(
                    request, f"Добро пожаловать, {user.get_full_name() or user.email}!"
                )
                return redirect("home")

    # Инициализируем формы если они не были созданы выше
    if register_form is None:
        register_form = UserRegistrationForm()
    if login_form is None:
        login_form = AuthenticationForm(request)

    return render(
        request,
        "users/auth.html",
        {
            "register_form": register_form,
            "login_form": login_form,
            "active_mode": active_mode,
        },
    )


def register(request):
    """Редирект на объединённую страницу авторизации."""
    return redirect(reverse("auth") + "?mode=register")


def login_view(request):
    """Редирект на объединённую страницу авторизации."""
    return redirect(reverse("auth") + "?mode=login")


@login_required_with_message(
    "Профиль игрока доступен только для зарегистрированных пользователей."
)
def profile(request, pk):
    """User profile view."""
    player = get_object_or_404(
        Player.objects.select_related(
            "user", "user__subscription", "user__subscription__tier"
        ),
        pk=pk,
    )

    from apps.tournaments.models import Match

    all_matches_qs = (
        Match.objects.filter(
            Q(player1=player)
            | Q(player2=player)
            | Q(team1__player1=player)
            | Q(team1__player2=player)
            | Q(team2__player1=player)
            | Q(team2__player2=player)
        )
        .select_related(
            "tournament",
            "player1",
            "player2",
            "winner",
            "team1",
            "team2",
            "winner_team",
        )
        .annotate(
            effective_date=Coalesce(
                "scheduled_datetime", "deadline", "completed_datetime"
            ),
        )
        .order_by("-effective_date")
    )

    # ---------- Фильтрация по месяцу/году/статусу ----------
    filter_year = request.GET.get("year")
    filter_month = request.GET.get("month")
    filter_status = request.GET.get("status")

    # Допустимые годы из реальных данных
    from django.db.models import Max, Min

    date_range = all_matches_qs.aggregate(
        min_date=Min("effective_date"),
        max_date=Max("effective_date"),
    )
    min_date = date_range["min_date"]
    max_date = date_range["max_date"]

    available_years: list[int] = []
    if min_date and max_date:
        available_years = list(range(max_date.year, min_date.year - 1, -1))

    # Применение фильтров
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
        # Проверяем что статус валидный
        valid_statuses = [s[0] for s in Match.MatchStatus.choices]
        if filter_status in valid_statuses:
            active_status = filter_status

    if active_year:
        all_matches_qs = all_matches_qs.filter(effective_date__year=active_year)
    if active_month and active_year:
        all_matches_qs = all_matches_qs.filter(effective_date__month=active_month)
    if active_status:
        all_matches_qs = all_matches_qs.filter(status=active_status)

    recent_matches = all_matches_qs

    MONTHS_RU = [
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

    progress_data = _get_profile_progress_data(player)

    # Собираем данные о сезонных очках по датам
    def _get_season_points_data(player: Player) -> list[dict[str, Any]]:
        """Собрать данные о сезонных очках по датам для графика."""
        from apps.tournaments.models import TournamentPlayerResult
        from apps.tournaments.season_utils import get_current_season

        current_season = get_current_season()
        # Получаем все результаты турниров игрока за текущий сезон
        fan_results = (
            TournamentPlayerResult.objects.filter(
                player=player,
                tournament__status="completed",
            )
            .select_related("tournament")
            .order_by(
                "tournament__end_date", "tournament__start_date", "tournament__pk"
            )
        )

        # Фильтруем только результаты за текущий сезон
        season_year = current_season.year

        events: list[tuple[date, int]] = []  # (date, points)

        for r in fan_results:
            tourn_date = r.tournament.end_date or r.tournament.start_date
            if not tourn_date:
                continue

            # Проверяем, что турнир в текущем сезоне
            if current_season.name == "Зима":
                # Зима: октябрь (год начала) - апрель (год начала + 1)
                if tourn_date.month >= 10 and tourn_date.year == season_year:
                    events.append((tourn_date, r.fan_points))
                elif tourn_date.month <= 4 and tourn_date.year == season_year + 1:
                    events.append((tourn_date, r.fan_points))
            else:  # Лето
                # Лето: май - сентябрь (один год)
                if (
                    tourn_date.month >= 5
                    and tourn_date.month <= 9
                    and tourn_date.year == season_year
                ):
                    events.append((tourn_date, r.fan_points))

        events.sort(key=lambda x: x[0])

        # Строим кумулятивный ряд
        start_date = current_season.start_month
        start_year = (
            season_year
            if current_season.name == "Лето" or start_date == 10
            else season_year + 1
        )
        if start_date == 10:  # Зима начинается в октябре
            season_start = date(start_year, 10, 1)
        else:  # Лето начинается в мае
            season_start = date(start_year, 5, 1)

        result = [{"date": season_start.isoformat(), "season_points": 0}]
        cum_points = 0

        for event_date, points in events:
            cum_points += points
            result.append(
                {
                    "date": event_date.isoformat(),
                    "season_points": cum_points,
                }
            )

        # Добавляем текущую дату с актуальными очками
        today = timezone.now().date()
        try:
            season_points_obj = player.season_points
            if (
                season_points_obj.season_name == current_season.name
                and season_points_obj.season_year == current_season.year
            ):
                current_points = season_points_obj.current_season_points
            else:
                current_points = 0
        except Exception:
            current_points = 0

        if not result or result[-1]["date"] != today.isoformat():
            result.append(
                {
                    "date": today.isoformat(),
                    "season_points": current_points,
                }
            )
        else:
            result[-1]["season_points"] = current_points

        return result

    season_points_data = _get_season_points_data(player)

    # Получаем сезонные очки
    from apps.tournaments.models import SeasonArchive, SeasonPoints
    from apps.tournaments.season_utils import get_current_season, get_season_display

    try:
        season_points = player.season_points
        current_season = get_current_season()
        # Проверяем, что сезон совпадает
        if (
            season_points.season_name != current_season.name
            or season_points.season_year != current_season.year
        ):
            # Создаём новую запись для нового сезона
            season_points = SeasonPoints.objects.create(
                player=player,
                current_season_points=0,
                season_name=current_season.name,
                season_year=current_season.year,
            )
    except SeasonPoints.DoesNotExist:
        current_season = get_current_season()
        season_points = SeasonPoints.objects.create(
            player=player,
            current_season_points=0,
            season_name=current_season.name,
            season_year=current_season.year,
        )

    current_season_display = get_season_display(current_season)

    # Получаем архивные результаты (чемпионские бейджи)
    season_championships = SeasonArchive.objects.filter(
        player=player,
        final_rank=1,
    ).order_by("-season_year", "-season_name")

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
        "available_years": available_years,
        "months_ru": MONTHS_RU,
        "active_year": active_year,
        "active_month": active_month,
        "active_status": active_status,
        "match_statuses": Match.MatchStatus.choices,
        "season_points": season_points,
        "current_season_display": current_season_display,
        "season_points_data": season_points_data,
        "season_championships": season_championships,
    }
    return render(request, "users/profile.html", context)


@login_required
def profile_edit(request):
    """Edit profile view."""
    try:
        player = request.user.player
    except Player.DoesNotExist:
        player = Player.objects.create(user=request.user)

    if request.method == "POST":
        form = PlayerProfileForm(
            request.POST, request.FILES, instance=player, user=request.user
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Профиль обновлён.")
            return redirect("profile", pk=player.pk)
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

    notes = Notification.objects.filter(user=request.user).order_by("-created_at")
    # mark all as read when viewed
    notes.filter(is_read=False).update(is_read=True)
    return render(request, "users/notifications.html", {"notifications": notes})


def ntrp_test(request):
    """Public strength level test page.

    Note: Test result can only be saved during registration.
    For registered users, the test is informational only and does not affect their rating.
    """
    # Test can only be saved during registration (via auth view), not after
    can_save = False
    return render(request, "users/ntrp_test.html", {"can_save": can_save})


@login_required
@require_POST
def save_ntrp(request):
    """Save strength level ONLY during registration.

    After registration, strength test results cannot be saved.
    Rating is determined solely by match results.
    """
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

    if level < Decimal("1.5") or level > Decimal("7.0"):
        return JsonResponse({"ok": False, "error": "out_of_range"}, status=400)

    try:
        player = request.user.player
    except Player.DoesNotExist:
        return JsonResponse({"ok": False, "error": "player_not_found"}, status=404)

    # CRITICAL: Do not allow saving test results after registration
    # Rating is determined only by match results after initial registration
    if player.matches_played > 0:
        return JsonResponse(
            {
                "ok": False,
                "error": "test_already_saved",
                "message": "Тест силы можно пройти только один раз при регистрации. Рейтинг формируется только по результатам матчей.",
            },
            status=403,
        )

    from .rating_utils import get_starting_points

    player.ntrp_level = level
    player.skill_level = _map_ntrp_to_skill_level(level)
    starting_pts = get_starting_points(level)
    player.total_points = starting_pts
    player.hidden_rating = float(starting_pts)

    player.save(
        update_fields=["ntrp_level", "skill_level", "total_points", "hidden_rating"]
    )
    return JsonResponse({"ok": True, "ntrp_level": f"{level:.1f}"})
