"""
Tournaments views.
"""

import json
from collections import defaultdict
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .fan import _is_fan, check_and_generate_past_deadline_brackets
from .round_robin import _is_round_robin, compute_standings, get_match_matrix
from .models import Match, MatchResultProposal, Tournament, TournamentTeam, TournamentType

MATCH_FORMAT_DESCRIPTIONS = {
    "1_set_6": "1 сет до 6 геймов. Матч до 6 выигранных геймов (при счёте 6:6 — игра до 7).",
    "1_set_tiebreak": "1 сет с тай-брейком. Матч до 6 геймов, при 6:6 — тай-брейк до 7 очков.",
    "2_sets": "2 сета до победы. Побеждает тот, кто выиграет 2 сета. При 1:1 — третий сет (тай-брейк).",
    "fast4": "2 коротких сета + супертай-брейк. Сеты до 4 геймов, при 1:1 — супертай-брейк до 10 очков.",
}
from .proposal_service import apply_proposal
from apps.users.models import Notification, Player, SkillLevel


def tournament_list(request):
    """List of tournaments."""
    check_and_generate_past_deadline_brackets()
    city = request.GET.get('city', '')
    category = request.GET.get('category', '')
    status = request.GET.get('status', '')

    tournaments = Tournament.objects.all().prefetch_related('participants__user')

    if city:
        tournaments = tournaments.filter(city__icontains=city)
    if category:
        tournaments = tournaments.filter(category=category)
    if status:
        tournaments = tournaments.filter(status=status)

    tournaments = tournaments.order_by('-start_date')

    context = {
        'tournaments': tournaments,
        'current_city': city,
        'current_category': category,
        'current_status': status,
        'category_choices': SkillLevel.choices,
    }
    return render(request, 'tournaments/list.html', context)


def tournament_detail(request, slug):
    """Tournament detail page."""
    tournament = get_object_or_404(
        Tournament.objects.prefetch_related(
            "matches__player1__user",
            "matches__player2__user",
            "matches__winner__user",
            "matches__team1__player1__user",
            "matches__team1__player2__user",
            "matches__team2__player1__user",
            "matches__team2__player2__user",
            "participants__user",
        ),
        slug=slug,
    )
    is_fan = _is_fan(tournament)
    is_round_robin = _is_round_robin(tournament)

    if is_fan:
        matches = tournament.matches.order_by("is_consolation", "round_index", "round_order")
        def round_key(m):
            return (m.round_name, m.is_consolation)
        matches_by_round = []
        for k, group in groupby(matches, key=round_key):
            matches_by_round.append((k[0], k[1], list(group)))
        matrix_participants = None
        matrix_data = None
        matrix_rows = None
        rr_standings = None
    elif is_round_robin:
        matches = tournament.matches.filter(is_consolation=False).order_by("round_index", "round_order")
        matches_by_round = []
        for _, g in groupby(matches, key=lambda m: m.round_index):
            round_matches = list(g)
            if round_matches:
                matches_by_round.append((round_matches[0].round_name, False, round_matches))
        matrix_participants, matrix_data = get_match_matrix(tournament)
        rr_standings = compute_standings(tournament)
        entity_id = lambda r: (r["team"] or r["player"]).id
        standings_by_entity = {entity_id(row): row for row in rr_standings}
        matrix_rows = []
        for i, p in enumerate(matrix_participants):
            row_cells = matrix_data[i] if i < len(matrix_data) else []
            st = standings_by_entity.get(p.id, {})
            matrix_rows.append({"participant": p, "cells": row_cells, "place": st.get("place"), "points": st.get("points")})
    else:
        matches_by_round = None
        matches = tournament.matches.all().order_by("-scheduled_datetime")
        matrix_participants = None
        matrix_data = None
        matrix_rows = None
        rr_standings = None

    if tournament.is_doubles():
        participants_qs = []
        for team in tournament.teams.select_related("player1__user", "player2__user").order_by("created_at"):
            participants_qs.append(team)
        solo_teams = [t for t in participants_qs if not t.player2_id]
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
        if is_fan:
            participants_qs = participants_qs.order_by("-total_points")
        else:
            participants_qs = participants_qs.order_by("user__last_name", "user__first_name")

    match_format_description = None
    if is_round_robin and tournament.match_format:
        match_format_description = MATCH_FORMAT_DESCRIPTIONS.get(
            tournament.match_format, tournament.get_match_format_display()
        )

    context = {
        "tournament": tournament,
        "matches": matches,
        "matches_by_round": matches_by_round,
        "matrix_participants": matrix_participants,
        "matrix_data": matrix_data,
        "matrix_rows": matrix_rows,
        "rr_standings": rr_standings,
        "is_fan": is_fan,
        "is_round_robin": is_round_robin,
        "match_format_description": match_format_description,
        "participants": participants_qs,
        "solo_teams": solo_teams,
        "can_join_team": can_join_team,
    }
    return render(request, "tournaments/detail.html", context)


def tournament_tables_list(request):
    """Страница «Турнирные таблицы» — список турниров с краткой статистикой."""
    tournaments = (
        Tournament.objects.all()
        .prefetch_related("participants__user", "matches", "fan_results")
        .order_by("-start_date")
    )
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
            int(100 * matches_completed / matches_total)
            if matches_total > 0
            else 0
        )
    context = {"tournaments": tournaments}
    return render(request, "tournaments/tables_list.html", context)


def tournament_tables_detail(request, slug):
    """Детальная страница турнирной таблицы: графики, диаграммы, полная статистика."""
    tournament = get_object_or_404(
        Tournament.objects.prefetch_related(
            "matches__player1__user",
            "matches__player2__user",
            "matches__winner__user",
            "matches__team1__player1__user",
            "matches__team1__player2__user",
            "matches__team2__player1__user",
            "matches__team2__player2__user",
            "participants__user",
            "fan_results__player__user",
        ),
        slug=slug,
    )
    is_fan = _is_fan(tournament)
    is_round_robin = _is_round_robin(tournament)
    if tournament.is_doubles():
        participants = []
        for team in tournament.teams.filter(player2__isnull=False).select_related("player1__user", "player2__user"):
            participants.extend([team.player1, team.player2])
        participants = list({p.id: p for p in participants}.values())
        participants.sort(key=lambda p: -p.total_points)
    else:
        participants = list(
            tournament.participants.select_related("user").order_by("-total_points")
        )

    if is_round_robin:
        standings = compute_standings(tournament)
        standings = [
            {
                "place": row["place"],
                "player": row["player"],
                "team": row.get("team"),
                "fan_result": None,
                "fan_points": row["points"],
                "round_eliminated": "—",
            }
            for row in standings
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
                    "round_eliminated": fr.get_round_eliminated_display() if fr else "—",
                }
            )

    # Матчи по раундам
    matches = tournament.matches.order_by("is_consolation", "round_index", "round_order")
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

    # Распределение очков FAN по раундам (для FAN)
    round_points = defaultdict(int)
    if is_fan:
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

    # Рейтинг участников (для гистограммы)
    ratings = [p.total_points for p in participants if p.total_points]
    ratings_sorted = sorted(ratings, reverse=True)[:20]  # топ-20
    ratings_labels = [f"Место {i}" for i in range(1, len(ratings_sorted) + 1)]

    context = {
        "tournament": tournament,
        "is_fan": is_fan,
        "is_round_robin": is_round_robin,
        "participants": participants,
        "standings": standings,
        "matches_by_round": matches_by_round,
        "chart_status_labels": json.dumps(chart_status_labels),
        "chart_status_data": json.dumps(chart_status_data),
        "chart_round_labels": json.dumps(chart_round_labels),
        "chart_round_data": json.dumps(chart_round_data),
        "ratings_sorted": json.dumps(ratings_sorted),
        "ratings_labels": json.dumps(ratings_labels),
        "participants_count": len(participants),
        "matches_total": main_matches.count(),
        "matches_completed": main_matches.filter(
            status__in=["completed", "walkover"]
        ).count(),
        "progress_pct": (
            int(100 * main_matches.filter(status__in=["completed", "walkover"]).count() / main_matches.count())
            if main_matches.count() > 0
            else 0
        ),
    }
    return render(request, "tournaments/tables_detail.html", context)


def champions_league(request):
    """Champions League page."""
    tournaments = Tournament.objects.filter(
        tournament_type=TournamentType.CHAMPIONS_LEAGUE
    ).order_by('-start_date')
    return render(request, 'tournaments/champions_league.html', {'tournaments': tournaments})


def match_detail(request, pk):
    """Match detail page."""
    match = get_object_or_404(
        Match.objects.select_related(
            "player1__user", "player2__user", "winner__user", "tournament", "court",
            "team1__player1__user", "team1__player2__user",
            "team2__player1__user", "team2__player2__user",
        ),
        pk=pk,
    )
    return render(
        request,
        "tournaments/match_detail.html",
        {"match": match, "is_fan": _is_fan(match.tournament)},
    )


@login_required
def my_matches(request):
    """List matches for current player."""

    player = getattr(request.user, 'player', None)
    if player is None:
        player = Player.objects.create(user=request.user)

    matches = Match.objects.filter(
        models.Q(player1=player) | models.Q(player2=player)
        | models.Q(team1__player1=player) | models.Q(team1__player2=player)
        | models.Q(team2__player1=player) | models.Q(team2__player2=player)
    ).select_related(
        'player1__user', 'player2__user', 'tournament',
        'team1__player1__user', 'team1__player2__user',
        'team2__player1__user', 'team2__player2__user',
    ).order_by('-scheduled_datetime')

    proposals = MatchResultProposal.objects.filter(match__in=matches).select_related('proposer', 'match')
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
        'tournaments/my_matches.html',
        {
            'matches': matches,
            'player': player,
        },
    )


@login_required
def propose_result(request, pk):
    """Propose result for a match by participant."""

    match = get_object_or_404(Match, pk=pk)
    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        messages.info(request, 'Матч уже завершён.')
        return redirect('my_matches')

    if request.method != 'POST':
        return redirect('my_matches')
    player = getattr(request.user, 'player', None)
    if player is None:
        messages.error(request, 'Создайте профиль игрока, чтобы предложить результат.')
        return redirect('profile_edit')

    if player not in (match.player1, match.player2):
        messages.error(request, 'Вы не участвуете в этом матче.')
        return redirect('my_matches')

    result = request.POST.get('result') or Match.ResultChoice.WIN

    MatchResultProposal.objects.filter(
        match=match,
        proposer=player,
        status=Match.ProposalStatus.PENDING,
    ).delete()

    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    proposal = MatchResultProposal.objects.create(
        match=match,
        proposer=player,
        result=result,
        player1_set1=_to_int(request.POST.get('p1s1')),
        player1_set2=_to_int(request.POST.get('p1s2')),
        player1_set3=_to_int(request.POST.get('p1s3')),
        player2_set1=_to_int(request.POST.get('p2s1')),
        player2_set2=_to_int(request.POST.get('p2s2')),
        player2_set3=_to_int(request.POST.get('p2s3')),
    )

    opponent = match.player2 if player == match.player1 else match.player1
    Notification.objects.create(
        user=opponent.user,
        message=f"{player} предложил результат матча в турнире {match.tournament.name}",
        url=reverse('my_matches'),
    )

    messages.success(request, 'Результат отправлен на подтверждение сопернику.')
    return redirect('my_matches')


@login_required
def confirm_proposal(request, pk):
    """Opponent confirms or rejects proposal."""

    proposal = get_object_or_404(
        MatchResultProposal.objects.select_related('match__player1', 'match__player2', 'proposer'),
        pk=pk,
    )

    if request.method != 'POST':
        return redirect('my_matches')

    match = proposal.match
    player = getattr(request.user, 'player', None)
    if player is None or player not in (match.player1, match.player2):
        messages.error(request, 'Вы не участвуете в этом матче.')
        return redirect('my_matches')

    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        messages.info(request, 'Матч уже завершён.')
        return redirect('my_matches')

    if proposal.status != Match.ProposalStatus.PENDING:
        messages.info(request, 'Этот результат уже обработан.')
        return redirect('my_matches')

    if proposal.proposer == player:
        messages.error(request, 'Вы не можете подтверждать свой же запрос.')
        return redirect('my_matches')

    action = request.POST.get('action')
    if action == 'accept':
        apply_proposal(proposal)
        # FAN-логика (advance, consolation, finalize) вызывается из post_save сигнала Match
        messages.success(request, "Результат подтверждён.")
    else:
        opponent = match.player2 if player == match.player1 else match.player1
        proposal.delete()
        Notification.objects.create(
            user=opponent.user,
            message=f'{player} отклонил результат матча. Введите свой результат.',
            url=reverse('my_matches'),
        )
        messages.info(request, 'Результат отклонён. Введите свой результат матча.')

    return redirect('my_matches')


def _check_tournament_registration_eligibility(request, tournament, player):
    """Проверка подписки и лимитов для регистрации. Возвращает (ok, error_message)."""
    user = getattr(request, "user", None)
    if user is None:
        return False, "Требуется авторизация."

    try:
        sub = user.subscription
        if not sub.is_valid():
            sub = None
    except Exception:
        sub = None

    if user.is_superuser or user.is_staff:
        return True, None

    if tournament.is_one_day:
        return True, None  # Payment flow handles it

    if not sub or not sub.is_active:
        return False, "Для участия в многодневных турнирах требуется подписка."

    if not sub.can_register_for_tournament():
        return False, "Вы исчерпали лимит регистрации на турниры в этом месяце. Обновите подписку."

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
    return tournament.teams.filter(
        Q(player1=player) | Q(player2=player)
    ).exists()


@login_required
def tournament_register(request, slug):
    """Register authenticated user to a tournament."""

    tournament = get_object_or_404(Tournament, slug=slug)
    player = getattr(request.user, 'player', None)
    if player is None:
        player = Player.objects.create(user=request.user)

    if getattr(tournament, "bracket_generated", False):
        messages.error(request, "Регистрация закрыта: сетка турнира уже сформирована.")
        return redirect("tournament_detail", slug=tournament.slug)

    if tournament.is_full():
        messages.error(request, "Регистрация закрыта: все места заняты.")
        return redirect("tournament_detail", slug=tournament.slug)

    # Парный турнир — отдельный поток регистрации
    if tournament.is_doubles():
        return redirect("tournament_register_doubles", slug=tournament.slug)

    # Check gender compatibility
    if tournament.gender != 'mixed':
        if (tournament.gender == 'male' and player.gender != 'male') or \
           (tournament.gender == 'female' and player.gender != 'female'):
            gender_text = 'мужской' if tournament.gender == 'male' else 'женский'
            messages.error(request, f'Этот турнир только для {gender_text} категории.')
            return redirect('tournament_detail', slug=tournament.slug)

    # Check if player is already registered
    if tournament.participants.filter(id=player.id).exists():
        messages.info(request, 'Вы уже зарегистрированы на этот турнир.')
        return redirect('tournament_detail', slug=tournament.slug)

    # SUBSCRIPTION CHECK
    try:
        sub = request.user.subscription
        if not sub.is_valid():
            sub = None
    except:
        sub = None

    # Admin bypass
    is_admin = request.user.is_superuser or request.user.is_staff

    ok, err = _check_tournament_registration_eligibility(request, tournament, player)
    if not ok and not is_admin:
        messages.error(request, err)
        if 'подписк' in (err or ''):
            return redirect('pricing')
        return redirect('tournament_detail', slug=tournament.slug)

    if is_admin:
         messages.success(request, 'Регистрация администратора (бесплатно/безлимитно).')
         tournament.participants.add(player)
         return redirect('tournament_detail', slug=tournament.slug)

    elif tournament.is_one_day:
        # One-day tournament: Redirect to payment preview
        from django.urls import reverse
        from urllib.parse import urlencode
        
        params = {'type': 'tournament', 'id': tournament.id}
        base_url = reverse('payment_preview')
        query_string = urlencode(params)
        return redirect(f'{base_url}?{query_string}')
        
    else:
        # Multi-day tournament: subscription already checked above
        try:
            sub = request.user.subscription
            if sub and sub.is_valid():
                sub.increment_usage()
                messages.success(request, f'Вы зарегистрированы! Осталось регистраций в этом месяце: {sub.get_remaining_slots()}')
            else:
                messages.success(request, 'Вы зарегистрированы!')
        except Exception:
            messages.success(request, 'Вы зарегистрированы!')
        tournament.participants.add(player)

    
    return redirect('tournament_detail', slug=tournament.slug)


@login_required
def tournament_register_doubles(request, slug):
    """Регистрация на парный турнир: solo, с партнёром или присоединение к существующей паре."""

    tournament = get_object_or_404(Tournament, slug=slug)
    if not tournament.is_doubles():
        return redirect("tournament_register", slug=slug)

    player = getattr(request.user, "player", None)
    if player is None:
        player = Player.objects.create(user=request.user)

    if tournament.bracket_generated:
        messages.error(request, "Регистрация закрыта: сетка турнира уже сформирована.")
        return redirect("tournament_detail", slug=slug)

    if tournament.is_full():
        messages.error(request, "Регистрация закрыта: все места заняты.")
        return redirect("tournament_detail", slug=slug)

    if tournament.gender != "mixed":
        if (tournament.gender == "male" and player.gender != "male") or (
            tournament.gender == "female" and player.gender != "female"
        ):
            gender_text = "мужской" if tournament.gender == "male" else "женский"
            messages.error(request, f"Этот турнир только для {gender_text} категории.")
            return redirect("tournament_detail", slug=slug)

    if _is_player_registered_in_doubles(tournament, player):
        messages.info(request, "Вы уже зарегистрированы на этот турнир.")
        return redirect("tournament_detail", slug=slug)

    ok, err = _check_tournament_registration_eligibility(request, tournament, player)
    if not ok and not (request.user.is_superuser or request.user.is_staff):
        messages.error(request, err)
        if err and "подписк" in err:
            return redirect("pricing")
        return redirect("tournament_detail", slug=slug)

    solo_teams = list(tournament.teams.filter(player2__isnull=True).select_related("player1__user"))
    partner_search_results = []

    if request.method == "GET" and request.GET.get("q"):
        q = request.GET.get("q", "").strip()
        if q:
            from django.db.models import Q
            filters = Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q) | Q(
                user__email__icontains=q
            ) | Q(user__phone__icontains=q)
            if str(q).isdigit():
                filters = filters | Q(id=int(q))
            partner_search_results = list(
                Player.objects.filter(filters)
                .exclude(id=player.id)
                .select_related("user")
                .distinct()[:10]
            )

    # POST: обработка выбора
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "solo":
            TournamentTeam.objects.create(tournament=tournament, player1=player)
            try:
                sub = request.user.subscription
                if sub and sub.is_valid():
                    sub.increment_usage()
            except Exception:
                pass
            messages.success(request, "Вы зарегистрированы. Партнёр может присоединиться к вам со своей страницы турнира.")
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
    }
    return render(request, "tournaments/register_doubles.html", context)


def _do_join_team(request, tournament, player, team):
    """Присоединить игрока к существующей команде (solo)."""
    ok, err = _check_tournament_registration_eligibility(request, tournament, player)
    if not ok and not (request.user.is_superuser or request.user.is_staff):
        messages.error(request, err)
        return redirect("tournament_detail", slug=tournament.slug)
    team.player2 = player
    team.save()
    try:
        sub = request.user.subscription
        if sub and sub.is_valid():
            sub.increment_usage()
    except Exception:
        pass
    Notification.objects.create(
        user=team.player1.user,
        message=f"{player} присоединился к вашей команде в турнире {tournament.name}.",
        url=reverse("tournament_detail", args=[tournament.slug]),
    )
    messages.success(request, f"Вы присоединились к команде с {team.player1}. Регистрация завершена.")
    return redirect("tournament_detail", slug=tournament.slug)


def _do_add_partner(request, tournament, player, partner_id):
    """Создать команду с указанным партнёром."""
    try:
        partner = Player.objects.get(pk=partner_id)
    except Player.DoesNotExist:
        messages.error(request, "Игрок не найден.")
        return redirect("tournament_register_doubles", slug=tournament.slug)

    if partner.id == player.id:
        messages.error(request, "Нельзя добавить себя в пару.")
        return redirect("tournament_register_doubles", slug=tournament.slug)

    if _is_player_registered_in_doubles(tournament, partner):
        messages.error(request, f"{partner} уже зарегистрирован в этом турнире.")
        return redirect("tournament_register_doubles", slug=tournament.slug)

    ok, err = _check_tournament_registration_eligibility(request, tournament, player)
    if not ok and not (request.user.is_superuser or request.user.is_staff):
        messages.error(request, err)
        return redirect("tournament_detail", slug=tournament.slug)

    partner_ok, partner_err = _check_user_can_register_for_tournament(partner.user, tournament)
    if not partner_ok:
        messages.error(request, f"Партнёр не может участвовать: {partner_err}")
        return redirect("tournament_register_doubles", slug=tournament.slug)

    TournamentTeam.objects.create(tournament=tournament, player1=player, player2=partner)
    try:
        sub = request.user.subscription
        if sub and sub.is_valid():
            sub.increment_usage()
    except Exception:
        pass
    try:
        psub = partner.user.subscription
        if psub and psub.is_valid():
            psub.increment_usage()
    except Exception:
        pass
    Notification.objects.create(
        user=partner.user,
        message=f"{player} добавил вас в команду на турнир {tournament.name}.",
        url=reverse("tournament_detail", args=[tournament.slug]),
    )
    messages.success(request, f"Команда зарегистрирована: вы и {partner}.")
    return redirect("tournament_detail", slug=tournament.slug)


@login_required
def tournament_join_team(request, slug, team_id):
    """Присоединиться к команде (партнёр без пары)."""
    if request.method != "POST":
        return redirect("tournament_detail", slug=slug)
    tournament = get_object_or_404(Tournament, slug=slug)
    team = get_object_or_404(TournamentTeam, tournament=tournament, pk=team_id, player2__isnull=True)
    player = getattr(request.user, "player", None)
    if player is None:
        player = Player.objects.create(user=request.user)
    return _do_join_team(request, tournament, player, team)
