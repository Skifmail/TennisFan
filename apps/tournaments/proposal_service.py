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
    is_doubles = bool(match.team1_id and match.team2_id) or match.is_doubles_sparring()
    result = proposal.result
    won = result in (
        Match.ResultChoice.WIN,
        Match.ResultChoice.WALKOVER_WIN,
    )

    if match.is_doubles_sparring():
        side_a = (match.player1, match.partner1)
        side_b = (match.player2, match.partner2)
        proposer_side = side_a if proposer in side_a else side_b
        opponent_side = side_b if proposer in side_a else side_a
        winner_side = proposer_side if won else opponent_side
        loser_side = opponent_side if won else proposer_side
        winner = winner_side[0]
        loser = loser_side[0]
        return (
            winner,
            loser,
            result
            in (
                Match.ResultChoice.WALKOVER_WIN,
                Match.ResultChoice.WALKOVER_LOSS,
            ),
            None,
            None,
        )

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


def notify_participants_match_result_confirmed(
    match: Match, *, walkover: bool = False
) -> None:
    """
    Отправить уведомления участникам матча о подтверждённом результате (ЛК + Telegram).
    Вызывается из apply_proposal и из страницы управления турниром (админ ввёл результат).
    """
    import logging

    logger = logging.getLogger(__name__)
    winner = match.winner
    winner_team = getattr(match, "winner_team", None)
    url = reverse("match_detail", args=[match.pk])

    from .utils import get_match_participants

    participants = [
        p
        for p in get_match_participants(match)
        if p and not getattr(p, "is_bye", False) and getattr(p, "user_id", None)
    ]
    is_friendly = match.is_friendly_sparring()

    for p in participants:
        try:
            is_winner = (
                p == winner
                or (winner_team and p in (winner_team.player1, winner_team.player2))
                or (
                    match.is_doubles_sparring()
                    and winner in (match.player1, match.partner1)
                    and p in (match.player1, match.partner1)
                )
                or (
                    match.is_doubles_sparring()
                    and winner in (match.player2, match.partner2)
                    and p in (match.player2, match.partner2)
                )
            )
            if walkover:
                msg = (
                    "Результат матча подтверждён: тех. победа (соперник снялся)."
                    if is_winner
                    else "Результат матча подтверждён: тех. поражение."
                )
            else:
                base = (
                    "Результат матча подтверждён: вы выиграли."
                    if is_winner
                    else "Результат матча подтверждён: поражение."
                )
                if not is_friendly:
                    p.refresh_from_db()
                    changes = p.get_rating_changes()
                    fan = changes.get("fan", {})
                    delta = fan.get("delta") or 0
                    if delta != 0:
                        from apps.users.rating_utils import rating_to_ntrp_level

                        d_str = f"+{int(delta)}" if delta > 0 else str(int(delta))
                        base += (
                            f" Вам начислено {d_str} очков рейтинга."
                            if delta > 0
                            else f" У вас вычтено {abs(int(delta))} очков рейтинга."
                        )
                        rating_before = float(p.total_points) - float(delta)
                        ntrp_before = rating_to_ntrp_level(rating_before)
                        ntrp_after = rating_to_ntrp_level(float(p.total_points))
                        base += f" Сила: {ntrp_before:.1f} → {ntrp_after:.1f}."
                msg = base
            if len(msg) > 255:
                msg = msg[:252] + "..."
            Notification.objects.create(user=p.user, message=msg, url=url)
        except Exception as e:
            logger.warning(
                "notify_participants_match_result_confirmed for player %s: %s",
                getattr(p, "pk", None),
                e,
            )

    try:
        from apps.telegram_bot.notifications import (
            notify_result_confirmed_to_participants,
        )

        notify_result_confirmed_to_participants(match)
    except Exception as e:
        logger.warning("notify_result_confirmed_to_participants failed: %s", e)


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

    match.refresh_from_db()
    notify_participants_match_result_confirmed(match, walkover=is_walkover_retired)
