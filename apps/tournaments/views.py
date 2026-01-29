"""
Tournaments views.
"""

import json
from collections import defaultdict
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .fan import _is_fan, check_and_generate_past_deadline_brackets
from .models import Match, MatchResultProposal, Tournament, TournamentType
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
            "participants__user",
        ),
        slug=slug,
    )
    is_fan = _is_fan(tournament)
    if is_fan:
        matches = tournament.matches.order_by("is_consolation", "round_index", "round_order")
        from itertools import groupby

        def round_key(m):
            return (m.round_name, m.is_consolation)

        matches_by_round = []
        for k, group in groupby(matches, key=round_key):
            matches_by_round.append((k[0], k[1], list(group)))
    else:
        matches_by_round = None
        matches = tournament.matches.all().order_by("-scheduled_datetime")

    participants_qs = tournament.participants.all()
    if is_fan:
        participants_qs = participants_qs.order_by("-total_points")
    else:
        participants_qs = participants_qs.order_by("user__last_name", "user__first_name")
    context = {
        "tournament": tournament,
        "matches": matches,
        "matches_by_round": matches_by_round,
        "is_fan": is_fan,
        "participants": participants_qs,
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
            "participants__user",
            "fan_results__player__user",
        ),
        slug=slug,
    )
    is_fan = _is_fan(tournament)
    participants = list(
        tournament.participants.select_related("user").order_by("-total_points")
    )

    # FAN: итоговая таблица участников (место, очки, раунд вылета)
    fan_results = {}
    if is_fan:
        for r in tournament.fan_results.select_related("player__user"):
            fan_results[r.player_id] = r
    # Сортируем по очкам FAN (победитель первый), затем по рейтингу
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
            "player1__user", "player2__user", "winner__user", "tournament", "court"
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
        (models.Q(player1=player) | models.Q(player2=player))
    ).select_related('player1__user', 'player2__user', 'tournament').order_by('-scheduled_datetime')

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
        # Multi-day tournament: Requires subscription quota
        if not sub or not sub.is_active:
             messages.error(request, 'Для участия в многодневных турнирах требуется подписка.')
             return redirect('pricing')
        
        if not sub.can_register_for_tournament():
             messages.error(request, 'Вы исчерпали лимит регистрации на турниры в этом месяце. Обновите подписку.')
             return redirect('pricing')

        # Increment usage
        sub.increment_usage()
        messages.success(request, f'Вы зарегистрированы! Осталось регистраций в этом месяце: {sub.get_remaining_slots()}')
        tournament.participants.add(player)

    
    # next_url = request.GET.get('next') or request.META.get('HTTP_REFERER')
    return redirect('tournament_detail', slug=tournament.slug)
