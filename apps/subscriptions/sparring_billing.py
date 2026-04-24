"""Списание FANcoin за состоявшиеся спарринги."""

from typing import cast

from loguru import logger

from apps.sparring.models import TeamSide
from apps.subscriptions.fancoin import (
    SPARRING_DOUBLES_COST,
    SPARRING_SINGLES_COST,
    SPARRING_TEAM_COST,
)
from apps.tournaments.models import Match

from .models import FancoinTransaction, UserSubscription


def _charge_user_for_match(
    *, user_id: int, match: Match, amount: int, reason: FancoinTransaction.Reason
) -> None:
    """Списать FANcoin у пользователя за конкретный матч.

    Args:
        user_id (int): Идентификатор пользователя.
        match (Match): Завершённый матч.
        amount (int): Сумма списания.
        reason (FancoinTransaction.Reason): Причина списания.

    Returns:
        None: Функция выполняет списание и логирование.
    """
    if FancoinTransaction.objects.filter(
        user_id=user_id,
        match=match,
        reason=reason,
        direction=FancoinTransaction.Direction.CHARGE,
    ).exists():
        return
    subscription = (
        UserSubscription.objects.select_related("tier").filter(user_id=user_id).first()
    )
    if subscription is None or not subscription.is_valid():
        logger.warning(
            "Skip FANcoin charge: no valid subscription "
            f"(user_id={user_id}, match_id={match.pk}, reason={reason})"
        )
        return
    charged = subscription.spend_fancoin(amount, reason=reason, match=match)
    if not charged:
        logger.warning(
            "Skip FANcoin charge: insufficient balance "
            f"(user_id={user_id}, match_id={match.pk}, amount={amount}, "
            f"reason={reason}, balance={subscription.fancoin_balance})"
        )


def _charge_team_series(match: Match) -> None:
    """Списать FANcoin за командную серию один раз.

    Args:
        match (Match): Любой матч из командной серии.

    Returns:
        None: Функция выполняет списание для всех участников серии.
    """
    if match.sparring_team_request_id is None:
        return
    request = match.sparring_team_request
    if request is None:
        return
    if FancoinTransaction.objects.filter(
        doubles_request=request,
        reason=FancoinTransaction.Reason.SPARRING_TEAM,
        direction=FancoinTransaction.Direction.CHARGE,
    ).exists():
        return

    team_members = request.teams.filter(side__in=[TeamSide.AUTHOR, TeamSide.OPPONENT])
    for team in team_members:
        for member in team.members.select_related("player__user").all():
            user = member.player.user
            subscription = (
                UserSubscription.objects.select_related("tier")
                .filter(user=user)
                .first()
            )
            if subscription is None or not subscription.is_valid():
                continue
            charged = subscription.spend_fancoin(
                SPARRING_TEAM_COST,
                reason=FancoinTransaction.Reason.SPARRING_TEAM,
                match=match,
                doubles_request=request,
            )
            if not charged:
                logger.warning(
                    "Team FANcoin charge skipped: insufficient balance "
                    f"(user_id={user.pk}, match_id={match.pk}, request_id={request.pk})"
                )


def charge_fancoin_for_completed_match(match: Match) -> None:
    """Списать FANcoin за состоявшийся спарринг.

    Args:
        match (Match): Матч, для которого подтверждён результат.

    Returns:
        None: Функция списывает FANcoin согласно типу спарринга.
    """
    if not match.is_sparring() or match.status != Match.MatchStatus.COMPLETED:
        return

    if match.sparring_team_request_id:
        _charge_team_series(match)
        return

    if match.is_doubles_sparring():
        participant_ids = [
            match.player1.user_id if match.player1_id else None,
            match.partner1.user_id if match.partner1_id else None,
            match.player2.user_id if match.player2_id else None,
            match.partner2.user_id if match.partner2_id else None,
        ]
        for user_id in {pid for pid in participant_ids if pid is not None}:
            _charge_user_for_match(
                user_id=user_id,
                match=match,
                amount=SPARRING_DOUBLES_COST,
                reason=cast(
                    FancoinTransaction.Reason,
                    FancoinTransaction.Reason.SPARRING_DOUBLES,
                ),
            )
        return

    single_ids = [
        match.player1.user_id if match.player1_id else None,
        match.player2.user_id if match.player2_id else None,
    ]
    for user_id in {pid for pid in single_ids if pid is not None}:
        _charge_user_for_match(
            user_id=user_id,
            match=match,
            amount=SPARRING_SINGLES_COST,
            reason=cast(
                FancoinTransaction.Reason,
                FancoinTransaction.Reason.SPARRING_SINGLES,
            ),
        )
