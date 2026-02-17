"""
Сервис подтверждения результатов матчей.
Используется в views и при сохранении заявки в админке (сигнал).
"""

from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notification

from .models import Match, MatchResultProposal


def _compute_result(proposal: MatchResultProposal):
    """Определить победителя, проигравшего и walkover по заявке. Поддержка одиночных и парных."""
    match = proposal.match
    proposer = proposal.proposer
    is_doubles = match.team1_id and match.team2_id

    result = proposal.result
    if is_doubles:
        proposer_team = (
            match.team1
            if proposer in (match.team1.player1, match.team1.player2)
            else match.team2
        )
        opponent_team = match.team2 if proposer_team == match.team1 else match.team1
        won = result in (
            Match.ResultChoice.WIN,
            Match.ResultChoice.WALKOVER_WIN,
        )
        winner_team = proposer_team if won else opponent_team
        loser_team = opponent_team if won else proposer_team
        winner = winner_team.player1
        loser = loser_team.player1
    else:
        opponent = match.player2 if proposer == match.player1 else match.player1
        if proposer == match.player1:
            winner = (
                match.player1
                if result
                in (
                    Match.ResultChoice.WIN,
                    Match.ResultChoice.WALKOVER_WIN,
                )
                else match.player2
            )
        else:
            winner = (
                match.player2
                if result
                in (
                    Match.ResultChoice.WIN,
                    Match.ResultChoice.WALKOVER_WIN,
                )
                else match.player1
            )
        loser = opponent if winner == proposer else proposer
        winner_team = None
        loser_team = None

    walkover = result in (
        Match.ResultChoice.WALKOVER_WIN,
        Match.ResultChoice.WALKOVER_LOSS,
    )
    return winner, loser, walkover, winner_team, loser_team


def apply_proposal(proposal: MatchResultProposal) -> None:
    """
    Применить подтверждённую заявку к матчу.
    Обновляет матч (winner, winner_team, score, status), отклоняет остальные заявки, отправляет уведомления.
    """
    match = proposal.match
    winner, loser, walkover, winner_team, loser_team = _compute_result(proposal)

    # При тех.поражении / тех.победе записываем счёт 6:0 6:0 в пользу победителя
    is_walkover_retired = proposal.result in (
        Match.ResultChoice.WALKOVER_LOSS,
        Match.ResultChoice.WALKOVER_WIN,
    )
    if is_walkover_retired:
        # Определяем, кто победитель для записи счёта 6:0 6:0
        if winner == match.player1 or (winner_team and winner_team == match.team1):
            match.player1_set1 = 6
            match.player2_set1 = 0
            match.player1_set2 = 6
            match.player2_set2 = 0
            match.player1_set3 = None
            match.player2_set3 = None
        else:
            match.player1_set1 = 0
            match.player2_set1 = 6
            match.player1_set2 = 0
            match.player2_set2 = 6
            match.player1_set3 = None
            match.player2_set3 = None
    else:
        # Для обычных матчей используем счёт из proposal
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
    if winner_team is not None:
        match.winner_team = winner_team
    match.status = (
        Match.MatchStatus.WALKOVER if walkover else Match.MatchStatus.COMPLETED
    )
    match.completed_datetime = (
        match.completed_datetime or match.scheduled_datetime or timezone.now()
    )

    # Mark match for FAN rating calculation
    match.rating_status = Match.RatingCalcStatus.PENDING
    match.save()

    match.result_proposals.exclude(pk=proposal.pk).update(
        status=Match.ProposalStatus.REJECTED
    )
    proposal.status = Match.ProposalStatus.ACCEPTED
    proposal.save(update_fields=["status"])

    url = reverse("match_detail", args=[match.pk])

    if is_walkover_retired:
        for p in (loser_team.player1, loser_team.player2) if loser_team else [loser]:
            if p and not getattr(p, "is_bye", False):
                Notification.objects.create(
                    user=p.user,
                    message="Результат матча подтверждён: тех. поражение.",
                    url=url,
                )
        for p in (
            (winner_team.player1, winner_team.player2) if winner_team else [winner]
        ):
            if p and not getattr(p, "is_bye", False):
                Notification.objects.create(
                    user=p.user,
                    message="Результат матча подтверждён: тех. победа (соперник снялся).",
                    url=url,
                )
    else:
        for p in (
            (winner_team.player1, winner_team.player2) if winner_team else [winner]
        ):
            if p and not getattr(p, "is_bye", False):
                Notification.objects.create(
                    user=p.user,
                    message="Результат матча подтверждён: вы выиграли.",
                    url=url,
                )
        for p in (loser_team.player1, loser_team.player2) if loser_team else [loser]:
            if p and not getattr(p, "is_bye", False):
                Notification.objects.create(
                    user=p.user,
                    message="Результат матча подтверждён: поражение.",
                    url=url,
                )
