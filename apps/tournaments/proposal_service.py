"""
Сервис подтверждения результатов матчей.
Используется в views и при сохранении заявки в админке (сигнал).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import reverse
from django.utils import timezone

from apps.subscriptions.sparring_billing import charge_fancoin_for_completed_match
from apps.users.models import Notification

from .models import Match, MatchResultProposal

if TYPE_CHECKING:
    from apps.users.models import Player


class ProposalValidationError(ValueError):
    """Несогласованный или неполный счёт в заявке на результат матча."""


def count_sets_won_from_score(
    player1_set1: int | None,
    player2_set1: int | None,
    player1_set2: int | None,
    player2_set2: int | None,
    player1_set3: int | None,
    player2_set3: int | None,
) -> tuple[int, int]:
    """Подсчитать выигранные сеты у стороны 1 и 2.

    Args:
        player1_set1: Геймы стороны 1 в 1-м сете.
        player2_set1: Геймы стороны 2 в 1-м сете.
        player1_set2: Геймы стороны 1 во 2-м сете.
        player2_set2: Геймы стороны 2 во 2-м сете.
        player1_set3: Геймы стороны 1 в 3-м сете.
        player2_set3: Геймы стороны 2 в 3-м сете.

    Returns:
        Кортеж (сеты_п1, сеты_п2).

    Raises:
        ProposalValidationError: Если в сете указаны геймы только одной стороны.
    """
    sets_p1 = 0
    sets_p2 = 0
    for games_p1, games_p2 in (
        (player1_set1, player2_set1),
        (player1_set2, player2_set2),
        (player1_set3, player2_set3),
    ):
        if games_p1 is None and games_p2 is None:
            continue
        if games_p1 is None or games_p2 is None:
            raise ProposalValidationError(
                "В каждом сете укажите геймы обеих сторон или оставьте сет пустым.",
            )
        if games_p1 > games_p2:
            sets_p1 += 1
        elif games_p2 > games_p1:
            sets_p2 += 1
    return sets_p1, sets_p2


def _proposer_is_side1(match: Match, proposer: Player) -> bool:
    """Проверить, играет ли инициатор заявки за сторону 1 матча.

    Args:
        match: Матч.
        proposer: Игрок, внёсший результат.

    Returns:
        True, если инициатор относится к player1/team1.
    """
    if match.team1_id and match.team2_id:
        return bool(proposer in (match.team1.player1, match.team1.player2))
    if match.is_doubles_sparring():
        return bool(proposer in (match.player1, match.partner1))
    return bool(proposer == match.player1)


def derive_proposer_result_from_score(
    match: Match,
    proposer: Player,
    *,
    player1_set1: int | None,
    player2_set1: int | None,
    player1_set2: int | None,
    player2_set2: int | None,
    player1_set3: int | None,
    player2_set3: int | None,
) -> str:
    """Определить WIN/LOSS инициатора по введённому счёту.

    Args:
        match: Матч.
        proposer: Игрок, внёсший результат.
        player1_set1: Геймы стороны 1 в 1-м сете.
        player2_set1: Геймы стороны 2 в 1-м сете.
        player1_set2: Геймы стороны 1 во 2-м сете.
        player2_set2: Геймы стороны 2 во 2-м сете.
        player1_set3: Геймы стороны 1 в 3-м сете.
        player2_set3: Геймы стороны 2 в 3-м сете.

    Returns:
        Значение ``Match.ResultChoice`` (WIN или LOSS).

    Raises:
        ProposalValidationError: Если счёт неполный или ничейный по сетам.
    """
    if player1_set1 is None or player2_set1 is None:
        raise ProposalValidationError(
            "Укажите счёт первого сета (геймы обеих сторон).",
        )
    sets_p1, sets_p2 = count_sets_won_from_score(
        player1_set1,
        player2_set1,
        player1_set2,
        player2_set2,
        player1_set3,
        player2_set3,
    )
    if sets_p1 == sets_p2:
        raise ProposalValidationError(
            "По введённому счёту нельзя определить победителя. "
            "Проверьте геймы в сетах.",
        )
    is_side1 = _proposer_is_side1(match, proposer)
    proposer_won = (sets_p1 > sets_p2) if is_side1 else (sets_p2 > sets_p1)
    return str(Match.ResultChoice.WIN if proposer_won else Match.ResultChoice.LOSS)


def format_proposal_score(proposal: MatchResultProposal) -> str:
    """Сформировать человекочитаемый счёт заявки.

    Args:
        proposal: Заявка на результат.

    Returns:
        Строка вида ``6:2 6:1`` или ``—``.
    """
    parts: list[str] = []
    for set_index in range(1, 4):
        games_p1 = getattr(proposal, f"player1_set{set_index}")
        games_p2 = getattr(proposal, f"player2_set{set_index}")
        if games_p1 is not None and games_p2 is not None:
            parts.append(f"{games_p1}:{games_p2}")
    return " ".join(parts) if parts else "—"


def validate_proposal_score_consistency(proposal: MatchResultProposal) -> None:
    """Проверить согласованность счёта и выбранного победителя в заявке.

    Args:
        proposal: Заявка на результат матча.

    Returns:
        None

    Raises:
        ProposalValidationError: Если победитель не соответствует счёту.
    """
    if proposal.result in (
        Match.ResultChoice.WALKOVER_WIN,
        Match.ResultChoice.WALKOVER_LOSS,
    ):
        return

    match = proposal.match
    sets_p1, sets_p2 = count_sets_won_from_score(
        proposal.player1_set1,
        proposal.player2_set1,
        proposal.player1_set2,
        proposal.player2_set2,
        proposal.player1_set3,
        proposal.player2_set3,
    )
    if proposal.player1_set1 is None or proposal.player2_set1 is None:
        raise ProposalValidationError(
            "Укажите счёт первого сета (геймы обеих сторон).",
        )
    if sets_p1 == sets_p2:
        raise ProposalValidationError(
            "По введённому счёту нельзя определить победителя.",
        )

    winner, _, walkover, winner_team, _ = _compute_result(proposal)
    if walkover:
        return

    is_doubles = bool(match.team1_id and match.team2_id)
    winner_won_more = (
        (not is_doubles and winner == match.player1 and sets_p1 > sets_p2)
        or (not is_doubles and winner == match.player2 and sets_p2 > sets_p1)
        or (
            is_doubles
            and winner_team is not None
            and winner_team.pk == match.team1_id
            and sets_p1 > sets_p2
        )
        or (
            is_doubles
            and winner_team is not None
            and winner_team.pk == match.team2_id
            and sets_p2 > sets_p1
        )
    )
    if not winner_won_more:
        raise ProposalValidationError(
            "Счёт не соответствует выбранному победителю. "
            "Проверьте геймы в сетах и выбор «Кто победил?».",
        )


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

    try:
        from apps.player_ratings.notifications import notify_players_to_rate_match

        notify_players_to_rate_match(match)
    except Exception as e:
        logger.warning("notify_players_to_rate_match failed: %s", e)


def apply_proposal(proposal: MatchResultProposal) -> None:
    """
    Применить подтверждённую заявку к матчу.
    Обновляет матч (winner, winner_team, score, status), отклоняет остальные заявки, отправляет уведомления.
    """
    validate_proposal_score_consistency(proposal)
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
    charge_fancoin_for_completed_match(match)

    match.result_proposals.exclude(pk=proposal.pk).update(
        status=Match.ProposalStatus.REJECTED
    )
    proposal.status = Match.ProposalStatus.ACCEPTED
    proposal.save(update_fields=["status"])

    match.refresh_from_db()
    notify_participants_match_result_confirmed(match, walkover=is_walkover_retired)
