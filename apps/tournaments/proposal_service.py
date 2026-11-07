"""
Сервис подтверждения результатов матчей.
Используется в views и при сохранении заявки в админке (сигнал).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.notifications.services import create_notification
from apps.subscriptions.sparring_billing import charge_fancoin_for_completed_match

from .match_locking import lock_match, lock_match_participants
from .models import Match, MatchResultProposal, TournamentTeam
from .score_validation import ProposalValidationError as ProposalValidationError
from .score_validation import validate_match_score
from .utils import (
    find_blocking_earlier_tournament_match,
    format_tournament_match_order_block_message,
    get_match_opponent_users,
    get_match_participants,
    get_match_player_side,
)

if TYPE_CHECKING:
    from apps.users.models import Player


NO_SHOW_HINT = (
    "Матч не состоялся? Не вводите счёт 0:0 — выберите «Тех. победа: соперник "
    "не явился» или «Тех. поражение: я не явился». Неявившейся стороне "
    "автоматически спишется 40 очков рейтинга."
)


def is_all_zero_score(
    player1_set1: int | None,
    player2_set1: int | None,
    player1_set2: int | None,
    player2_set2: int | None,
    player1_set3: int | None,
    player2_set3: int | None,
) -> bool:
    """Проверить, что во всех заполненных сетах стоит 0:0.

    Такой «технический» счёт игроки пытаются вводить при неявке соперника.

    Args:
        player1_set1: Геймы стороны 1 в 1-м сете.
        player2_set1: Геймы стороны 2 в 1-м сете.
        player1_set2: Геймы стороны 1 во 2-м сете.
        player2_set2: Геймы стороны 2 во 2-м сете.
        player1_set3: Геймы стороны 1 в 3-м сете.
        player2_set3: Геймы стороны 2 в 3-м сете.

    Returns:
        True, если хотя бы один сет заполнен и все заполненные сеты равны 0:0.
    """
    values = (
        player1_set1,
        player2_set1,
        player1_set2,
        player2_set2,
        player1_set3,
        player2_set3,
    )
    filled = [value for value in values if value is not None]
    return bool(filled) and all(value == 0 for value in filled)


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
    return get_match_player_side(match, proposer) == 1


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
    if is_all_zero_score(
        player1_set1,
        player2_set1,
        player1_set2,
        player2_set2,
        player1_set3,
        player2_set3,
    ):
        raise ProposalValidationError(NO_SHOW_HINT)
    validate_match_score(
        match,
        (
            (player1_set1, player2_set1),
            (player1_set2, player2_set2),
            (player1_set3, player2_set3),
        ),
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
    match = proposal.match
    if proposal.result not in Match.ResultChoice.values:
        raise ProposalValidationError("Выберите допустимый результат матча.")
    if get_match_player_side(match, proposal.proposer) is None:
        raise ProposalValidationError("Инициатор больше не участвует в этом матче.")
    if proposal.result in (
        Match.ResultChoice.WALKOVER_WIN,
        Match.ResultChoice.WALKOVER_LOSS,
    ):
        return

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
    if is_all_zero_score(
        proposal.player1_set1,
        proposal.player2_set1,
        proposal.player1_set2,
        proposal.player2_set2,
        proposal.player1_set3,
        proposal.player2_set3,
    ):
        raise ProposalValidationError(NO_SHOW_HINT)
    validate_match_score(
        match,
        (
            (proposal.player1_set1, proposal.player2_set1),
            (proposal.player1_set2, proposal.player2_set2),
            (proposal.player1_set3, proposal.player2_set3),
        ),
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


def _compute_result(
    proposal: MatchResultProposal,
) -> tuple[
    Player | None, Player | None, bool, TournamentTeam | None, TournamentTeam | None
]:
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
        team1, team2 = match.team1, match.team2
        if team1 is None or team2 is None:
            raise ProposalValidationError("Состав сторон матча не завершён.")
        proposer_team = team1 if proposer in (team1.player1, team1.player2) else team2
        opponent_team = team2 if proposer_team == team1 else team1
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


def result_summary_for_player(match: Match, player: Player) -> str:
    """Описать исход со стороны получателя и дельту именно этого матча."""
    side = get_match_player_side(match, player)
    winner_side = get_match_player_side(match, match.winner) if match.winner else None
    is_winner = side is not None and side == winner_side
    if match.is_deadline_auto_rt():
        return "Дедлайн истёк: RT 0:0. Рейтинг не меняется."
    if match.is_no_show_walkover():
        no_show_sides = match.get_no_show_sides()
        missed = side is not None and no_show_sides[side - 1]
        if match.is_mutual_no_show_walkover():
            outcome = "Матч не состоялся: неявка обеих сторон."
        elif is_winner:
            outcome = "Вы выиграли: соперник не явился."
        else:
            outcome = "Вы проиграли: вам засчитана неявка."
        return outcome + (
            " Штраф за неявку: −40 очков рейтинга."
            if missed
            else " Рейтинг не меняется."
        )

    outcome = "Вы выиграли." if is_winner else "Вы проиграли."
    if match.is_friendly_sparring():
        return outcome + " Рейтинг не меняется."
    if match.rating_status == Match.RatingCalcStatus.CALCULATED:
        delta = match.rating_delta_player1 if side == 1 else match.rating_delta_player2
        return outcome + f" Изменение рейтинга за этот матч: {delta:+.0f} очков."
    if match.rating_status == Match.RatingCalcStatus.PENDING:
        return outcome + " Рейтинг ожидает расчёта."
    return outcome


def notify_participants_match_result_confirmed(
    match: Match, *, walkover: bool = False
) -> None:
    """Сохранить одно уведомление результата на участника; Telegram отправляет очередь."""
    url = reverse("match_detail", args=[match.pk])

    participants = [
        p
        for p in get_match_participants(match)
        if p and not getattr(p, "is_bye", False) and getattr(p, "user_id", None)
    ]
    for p in participants:
        msg = (
            f"Результат матча применён. Матч №{match.pk}. "
            f"{result_summary_for_player(match, p)}"
        )
        if len(msg) > 255:
            msg = msg[:252] + "..."
        create_notification(
            user=p.user, message=msg, url=url, event_key="match.result_confirmed"
        )

    from apps.player_ratings.notifications import notify_players_to_rate_match
    from apps.telegram_bot.notifications import notify_result_confirmed_to_participants

    # Сохранение обязательных уведомлений — часть результата, а не внешний best effort.
    notify_result_confirmed_to_participants(match)
    notify_players_to_rate_match(match)


def no_show_sides_from_proposal(proposal: MatchResultProposal) -> tuple[bool, bool]:
    """Определить стороны неявки по заявке игрока.

    ``WALKOVER_LOSS`` — не явился сам инициатор, ``WALKOVER_WIN`` — соперник.

    Args:
        proposal: Заявка с результатом тех. победы или тех. поражения.

    Returns:
        Пара флагов (неявка_стороны_1, неявка_стороны_2).
    """
    proposer_is_side1 = _proposer_is_side1(proposal.match, proposal.proposer)
    proposer_no_show = proposal.result == Match.ResultChoice.WALKOVER_LOSS
    side1_no_show = proposer_is_side1 == proposer_no_show
    return side1_no_show, not side1_no_show


def _apply_no_show_from_proposal(proposal: MatchResultProposal) -> None:
    """Проставить Walkover (неявку) по заявке игрока: штраф только неявившемуся.

    Args:
        proposal: Заявка с результатом тех. победы или тех. поражения.

    Raises:
        ProposalValidationError: Если неявку нельзя проставить для этого матча.
    """
    from .overdue import apply_no_show_walkover

    side1_no_show, side2_no_show = no_show_sides_from_proposal(proposal)
    try:
        apply_no_show_walkover(
            proposal.match,
            side1_no_show=side1_no_show,
            side2_no_show=side2_no_show,
            notify=False,
        )
    except ValueError as exc:
        raise ProposalValidationError(str(exc)) from exc


def _apply_played_result(proposal: MatchResultProposal) -> None:
    """Записать в матч сыгранный результат со счётом по сетам."""
    match = proposal.match
    winner, _loser, _walkover, winner_team, _loser_team = _compute_result(proposal)

    for field in (
        "player1_set1",
        "player2_set1",
        "player1_set2",
        "player2_set2",
        "player1_set3",
        "player2_set3",
    ):
        setattr(match, field, getattr(proposal, field))

    match.winner = winner
    if winner_team is not None:
        match.winner_team = winner_team
    match.status = Match.MatchStatus.COMPLETED
    match.completed_datetime = (
        match.completed_datetime or match.scheduled_datetime or timezone.now()
    )
    match.rating_status = Match.RatingCalcStatus.PENDING
    match.save()


@transaction.atomic
def apply_proposal(proposal: MatchResultProposal) -> bool:
    """Однократно применить свежую заявку под блокировкой матча.

    Результат, рейтинг, списание, ЛК и очередь Telegram фиксируются вместе.
    Повтор уже применённой заявки возвращает False, даже если матч снова открыт.
    Принятый админкой статус без applied_at допускает первое применение.
    """
    if not proposal.pk:
        raise ProposalValidationError("Сначала сохраните заявку на результат.")
    match = lock_match(proposal.match_id)
    if match is None:
        raise ProposalValidationError("Матч не найден.")
    fresh = MatchResultProposal.objects.select_for_update().get(
        pk=proposal.pk, match_id=match.pk
    )
    fresh.match = match
    if fresh.applied_at is not None:
        proposal.status = fresh.status
        proposal.applied_at = fresh.applied_at
        proposal.match = match
        return False
    if fresh.status not in (
        Match.ProposalStatus.PENDING,
        Match.ProposalStatus.ACCEPTED,
    ):
        raise ProposalValidationError("Эта заявка уже отклонена.")
    if match.status not in (Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS):
        raise ProposalValidationError(
            "Матч уже завершён, отменён или ещё не готов к игре."
        )

    # Единый порядок блокировок защищает рейтинг игроков и состав пар.
    lock_match_participants(match)
    participants = get_match_participants(match)
    if len(participants) < 2 or match.is_bye_match():
        raise ProposalValidationError("В матче должны быть две полноценные стороны.")
    if bool(match.team1_id) != bool(match.team2_id) or bool(match.partner1_id) != bool(
        match.partner2_id
    ):
        raise ProposalValidationError("Состав сторон матча не завершён.")
    validate_proposal_score_consistency(fresh)
    is_no_show = fresh.result in (
        Match.ResultChoice.WALKOVER_LOSS,
        Match.ResultChoice.WALKOVER_WIN,
    )
    if is_no_show:
        _apply_no_show_from_proposal(fresh)
    else:
        _apply_played_result(fresh)
    charge_fancoin_for_completed_match(match)
    match.result_proposals.filter(status=Match.ProposalStatus.PENDING).exclude(
        pk=fresh.pk
    ).update(status=Match.ProposalStatus.REJECTED)
    fresh.status = Match.ProposalStatus.ACCEPTED
    fresh.applied_at = timezone.now()
    if (confirmer := getattr(proposal, "_confirmed_by_user", None)) is not None:
        fresh._confirmed_by_user = confirmer
    fresh.save(update_fields=["status", "applied_at"])
    proposal.status = fresh.status
    proposal.applied_at = fresh.applied_at
    proposal.match = match
    match.refresh_from_db()
    notify_participants_match_result_confirmed(match, walkover=is_no_show)
    return True


@transaction.atomic
def submit_match_result(
    match_id: int,
    proposer: Player,
    *,
    result: str,
    player1_set1: int | None = None,
    player2_set1: int | None = None,
    player1_set2: int | None = None,
    player2_set2: int | None = None,
    player1_set3: int | None = None,
    player2_set3: int | None = None,
    score_from_proposer: bool = False,
) -> MatchResultProposal:
    """Создать заявку и сразу применить результат в одной транзакции.

    Полномочия, порядок матчей и ориентация счёта проверяются по свежему матчу.
    Отказ не оставляет заявку, событие активности или частично применённый счёт.
    """
    from .withdraw import is_player_withdrawn

    match = lock_match(match_id)
    if match is None:
        raise ProposalValidationError("Матч не найден.")
    side = get_match_player_side(match, proposer)
    if side is None:
        raise ProposalValidationError("Вы больше не участвуете в этом матче.")
    if match.status not in (Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS):
        raise ProposalValidationError(
            "Матч уже завершён, отменён или ещё не готов к игре."
        )
    if match.result_proposals.filter(status=Match.ProposalStatus.PENDING).exists():
        raise ProposalValidationError("По этому матчу уже внесён результат.")
    tournament = match.tournament
    if tournament is not None and is_player_withdrawn(tournament, proposer):
        raise ProposalValidationError(
            "Вы сняты с турнира и не можете вносить результаты матчей."
        )
    if blocking := find_blocking_earlier_tournament_match(match, proposer):
        raise ProposalValidationError(
            format_tournament_match_order_block_message(blocking, proposer)
        )
    if result not in Match.ResultChoice.values:
        raise ProposalValidationError("Выберите допустимый результат матча.")
    if score_from_proposer and side == 2:
        player1_set1, player2_set1 = player2_set1, player1_set1
        player1_set2, player2_set2 = player2_set2, player1_set2
        player1_set3, player2_set3 = player2_set3, player1_set3
    score_fields = {
        "player1_set1": player1_set1,
        "player2_set1": player2_set1,
        "player1_set2": player1_set2,
        "player2_set2": player2_set2,
        "player1_set3": player1_set3,
        "player2_set3": player2_set3,
    }
    if result not in (
        Match.ResultChoice.WALKOVER_WIN,
        Match.ResultChoice.WALKOVER_LOSS,
    ):
        result = derive_proposer_result_from_score(match, proposer, **score_fields)
    proposal = MatchResultProposal(
        match=match, proposer=proposer, result=result, **score_fields
    )
    validate_proposal_score_consistency(proposal)
    proposal.save()
    apply_proposal(proposal)
    return proposal


@transaction.atomic
def resolve_result_proposal(
    proposal_id: int, player: Player, *, accept: bool
) -> MatchResultProposal:
    """Обработать старую заявку только свежим соперником, сохранив историю отклонения."""
    from apps.core.activity import log_activity
    from apps.core.models import PlatformActivityEvent
    from apps.telegram_bot.notifications import notify_proposal_rejected

    match_id = (
        MatchResultProposal.objects.filter(pk=proposal_id)
        .values_list("match_id", flat=True)
        .first()
    )
    if match_id is None:
        raise ProposalValidationError("Предложение не найдено.")
    match = lock_match(match_id)
    if match is None:
        raise ProposalValidationError("Матч не найден.")
    proposal = MatchResultProposal.objects.select_for_update().get(pk=proposal_id)
    proposal.match = match
    if (
        proposal.applied_at is not None
        or proposal.status != Match.ProposalStatus.PENDING
    ):
        raise ProposalValidationError("Этот результат уже обработан.")
    if match.status not in (Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS):
        raise ProposalValidationError(
            "Матч уже завершён, отменён или ещё не готов к игре."
        )
    if get_match_player_side(match, player) is None:
        raise ProposalValidationError("Вы больше не участвуете в этом матче.")
    if player.user not in get_match_opponent_users(match, proposal.proposer):
        raise ProposalValidationError(
            "Подтвердить или отклонить результат может только соперник."
        )
    if accept:
        proposal._confirmed_by_user = player.user
        apply_proposal(proposal)
    else:
        proposal.status = Match.ProposalStatus.REJECTED
        proposal.save(update_fields=["status"])
        log_activity(
            event_type=PlatformActivityEvent.EventType.MATCH_RESULT_REJECTED,
            actor=player.user,
            description=f"Отклонил результат матча: {match}",
            target_url=reverse("match_detail", args=[match.pk]),
            dedupe_key=f"proposal_rejected:{proposal.pk}",
        )
        create_notification(
            user=proposal.proposer.user,
            message=f"{player} отклонил результат матча. Введите свой результат.",
            url=reverse("my_matches"),
            event_key="match.proposal_updated",
        )
        notify_proposal_rejected(proposal)
    return proposal
