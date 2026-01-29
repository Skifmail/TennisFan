"""
Сервис подтверждения результатов матчей.
Используется в views и при сохранении заявки в админке (сигнал).
"""

from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notification

from .fan import _is_fan
from .models import Match, MatchResultProposal


def _compute_result(proposal: MatchResultProposal):
    """Определить победителя, проигравшего и walkover по заявке."""
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


def apply_proposal(proposal: MatchResultProposal) -> None:
    """
    Применить подтверждённую заявку к матчу.
    Обновляет матч (winner, score, status), отклоняет остальные заявки, отправляет уведомления.
    """
    match = proposal.match
    winner, loser, walkover = _compute_result(proposal)

    for field in [
        "player1_set1", "player2_set1", "player1_set2", "player2_set2",
        "player1_set3", "player2_set3",
    ]:
        setattr(match, field, getattr(proposal, field))
    match.winner = winner
    match.status = Match.MatchStatus.WALKOVER if walkover else Match.MatchStatus.COMPLETED
    match.completed_datetime = match.completed_datetime or match.scheduled_datetime or timezone.now()

    if not _is_fan(match.tournament):
        win_delta = getattr(match.tournament, "points_winner", 100)
        lose_delta = getattr(match.tournament, "points_loser", -50)
        if winner == match.player1:
            match.points_player1 = win_delta
            match.points_player2 = lose_delta
        else:
            match.points_player1 = lose_delta
            match.points_player2 = win_delta
    match.save()

    match.result_proposals.exclude(pk=proposal.pk).update(status=Match.ProposalStatus.REJECTED)
    proposal.status = Match.ProposalStatus.ACCEPTED
    proposal.save(update_fields=["status"])

    url = reverse("match_detail", args=[match.pk])
    Notification.objects.create(
        user=winner.user,
        message="Результат матча подтверждён: вы выиграли.",
        url=url,
    )
    Notification.objects.create(
        user=loser.user,
        message="Результат матча подтверждён: поражение.",
        url=url,
    )
