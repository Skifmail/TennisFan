"""
Tournaments views.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import Match, MatchResultProposal, Tournament, TournamentType
from apps.users.models import Notification, Player, PlayerCategory


def tournament_list(request):
    """List of tournaments."""
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
        'category_choices': PlayerCategory.choices,
    }
    return render(request, 'tournaments/list.html', context)


def tournament_detail(request, slug):
    """Tournament detail page."""
    tournament = get_object_or_404(
        Tournament.objects.prefetch_related(
            'matches__player1__user', 
            'matches__player2__user',
            'participants__user'
        ),
        slug=slug
    )
    context = {
        'tournament': tournament,
        'matches': tournament.matches.all().order_by('-scheduled_datetime'),
        'participants': tournament.participants.all().order_by('user__last_name', 'user__first_name'),
    }
    return render(request, 'tournaments/detail.html', context)


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
            'player1__user', 'player2__user', 'winner__user', 'tournament', 'court'
        ),
        pk=pk
    )
    return render(request, 'tournaments/match_detail.html', {'match': match})


def _compute_result(proposal: MatchResultProposal):
    """Determine winner/loser and walkover based on proposal."""

    match = proposal.match
    proposer = proposal.proposer
    opponent = match.player2 if proposer == match.player1 else match.player1

    result = proposal.result
    if proposer == match.player1:
        winner = match.player1 if result in (
            Match.ResultChoice.WIN,
            Match.ResultChoice.WALKOVER_WIN,
        ) else match.player2
    else:
        winner = match.player2 if result in (
            Match.ResultChoice.WIN,
            Match.ResultChoice.WALKOVER_WIN,
        ) else match.player1

    loser = opponent if winner == proposer else proposer
    walkover = result in (
        Match.ResultChoice.WALKOVER_WIN,
        Match.ResultChoice.WALKOVER_LOSS,
    )
    return winner, loser, walkover


def _apply_proposal(proposal: MatchResultProposal):
    """Apply accepted proposal to match and update stats."""

    match = proposal.match
    winner, loser, walkover = _compute_result(proposal)

    # Update score
    for field in [
        "player1_set1",
        "player2_set1",
        "player1_set2",
        "player2_set2",
        "player1_set3",
        "player2_set3",
    ]:
        setattr(match, field, getattr(proposal, field))

    match.winner = winner
    match.status = Match.MatchStatus.WALKOVER if walkover else Match.MatchStatus.COMPLETED
    match.completed_datetime = match.completed_datetime or match.scheduled_datetime
    # Set per-match points for display
    win_delta = getattr(match.tournament, "points_winner", 100)
    lose_delta = getattr(match.tournament, "points_loser", -50)
    if winner == match.player1:
        match.points_player1 = win_delta
        match.points_player2 = lose_delta
    else:
        match.points_player1 = lose_delta
        match.points_player2 = win_delta
    match.save()

    # Update player statistics
    from apps.users.models import Player
    
    # Update winner stats
    winner_player = Player.objects.filter(user=winner.user).first()
    if winner_player:
        winner_player.total_points = winner_player.total_points + win_delta
        winner_player.matches_played = winner_player.matches_played + 1
        winner_player.matches_won = winner_player.matches_won + 1
        winner_player.save(update_fields=['total_points', 'matches_played', 'matches_won'])
    
    # Update loser stats
    loser_player = Player.objects.filter(user=loser.user).first()
    if loser_player:
        loser_player.total_points = max(0, loser_player.total_points + lose_delta)  # Don't go below 0
        loser_player.matches_played = loser_player.matches_played + 1
        loser_player.save(update_fields=['total_points', 'matches_played'])

    # Close other pending proposals for this match
    match.result_proposals.exclude(pk=proposal.pk).update(status=Match.ProposalStatus.REJECTED)

    proposal.status = Match.ProposalStatus.ACCEPTED
    proposal.save(update_fields=["status"])

    # Notifications
    Notification.objects.create(
        user=winner.user,
        message="Результат матча подтвержден: вы выиграли.",
        url=reverse('match_detail', args=[match.pk]),
    )
    Notification.objects.create(
        user=loser.user,
        message="Результат матча подтвержден: поражение.",
        url=reverse('match_detail', args=[match.pk]),
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
        _apply_proposal(proposal)
        messages.success(request, 'Результат подтверждён.')
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

    # Check if tournament is full
    if tournament.is_full():
        messages.error(request, 'Регистрация закрыта: все места заняты.')
        next_url = request.GET.get('next') or request.META.get('HTTP_REFERER')
        return redirect(next_url or reverse('tournament_detail', args=[tournament.slug]))

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
