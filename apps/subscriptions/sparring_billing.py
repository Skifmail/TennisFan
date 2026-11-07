"""Списание FANcoin за состоявшиеся спарринги."""

from __future__ import annotations

from django.db import transaction
from loguru import logger

from apps.sparring.models import DoublesMatchKind, DoublesMatchRequest, TeamSide
from apps.subscriptions.fancoin import (
    SPARRING_DOUBLES_COST,
    SPARRING_SINGLES_COST,
    SPARRING_TEAM_COST,
)
from apps.tournaments.models import Match

from .models import FancoinTransaction, UserSubscription


def _spend_for_sparring(
    subscription: UserSubscription,
    *,
    match: Match,
    amount: int,
    reason: FancoinTransaction.Reason,
    doubles_request: DoublesMatchRequest | None = None,
) -> bool:
    """Зафиксировать оплату или использование безлимита без последующего списания."""
    if not subscription.spend_fancoin(
        amount, reason=reason, match=match, doubles_request=doubles_request
    ):
        return False
    # spend_fancoin() не создаёт расход для безлимита. Нулевая запись закрывает
    # оплату этого матча/серии даже после истечения подписки или смены тарифа.
    FancoinTransaction.objects.get_or_create(
        user_id=subscription.user_id,
        match=match,
        reason=reason,
        direction=FancoinTransaction.Direction.CHARGE,
        defaults={
            "amount": 0,
            "balance_after": subscription.fancoin_balance,
            "doubles_request": doubles_request,
        },
    )
    return True


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
    subscription = (
        UserSubscription.objects.select_for_update(of=("self",), no_key=True)
        .filter(user_id=user_id)
        .first()
    )
    if FancoinTransaction.objects.filter(
        user_id=user_id,
        match=match,
        reason=reason,
        direction=FancoinTransaction.Direction.CHARGE,
    ).exists():
        return
    if subscription is None or not subscription.is_valid():
        logger.warning(
            "Skip FANcoin charge: no valid subscription "
            f"(user_id={user_id}, match_id={match.pk}, reason={reason})"
        )
        return
    charged = _spend_for_sparring(
        subscription, amount=amount, reason=reason, match=match
    )
    if not charged:
        logger.warning(
            "Skip FANcoin charge: insufficient balance "
            f"(user_id={user_id}, match_id={match.pk}, amount={amount}, "
            f"reason={reason}, balance={subscription.fancoin_balance})"
        )


@transaction.atomic
def _charge_team_series(match: Match) -> None:
    """Списать FANcoin за командную серию один раз.

    Args:
        match (Match): Любой матч из командной серии.

    Returns:
        None: Функция выполняет списание для всех участников серии.
    """
    if match.sparring_team_request_id is None:
        return
    request = (
        DoublesMatchRequest.objects.select_for_update(no_key=True)
        .filter(pk=match.sparring_team_request_id)
        .first()
    )
    if request is None or request.kind != DoublesMatchKind.TEAM:
        return
    user_ids = request.teams.filter(
        side__in=[TeamSide.AUTHOR, TeamSide.OPPONENT]
    ).values_list("members__player__user_id", flat=True)
    for user_id in sorted({value for value in user_ids if value is not None}):
        subscription = (
            UserSubscription.objects.select_for_update(of=("self",), no_key=True)
            .filter(user_id=user_id)
            .first()
        )
        if subscription is None or not subscription.is_valid():
            continue
        if FancoinTransaction.objects.filter(
            user_id=user_id,
            doubles_request=request,
            reason=FancoinTransaction.Reason.SPARRING_TEAM,
            direction=FancoinTransaction.Direction.CHARGE,
        ).exists():
            continue
        charged = _spend_for_sparring(
            subscription,
            amount=SPARRING_TEAM_COST,
            reason=FancoinTransaction.Reason(FancoinTransaction.Reason.SPARRING_TEAM),
            match=match,
            doubles_request=request,
        )
        if not charged:
            logger.warning(
                "Списание командного спарринга пропущено: user_id={}, match_id={}, request_id={}",
                user_id,
                match.pk,
                request.pk,
            )


@transaction.atomic
def charge_fancoin_for_completed_match(match: Match) -> None:
    """Списать FANcoin за состоявшийся спарринг.

    Args:
        match (Match): Матч, для которого подтверждён результат.

    Returns:
        None: Функция списывает FANcoin согласно типу спарринга.
    """
    from apps.tournaments.match_locking import lock_match

    fresh = lock_match(match.pk)
    if (
        fresh is None
        or not fresh.is_sparring()
        or fresh.status != Match.MatchStatus.COMPLETED
    ):
        return
    match = fresh

    series_request = match.sparring_team_request
    if series_request is not None and series_request.kind == DoublesMatchKind.TEAM:
        _charge_team_series(match)
        return

    if match.is_doubles_sparring():
        participant_ids = {
            player.user_id
            for player in (match.player1, match.partner1, match.player2, match.partner2)
            if player is not None
        }
        for user_id in sorted(participant_ids):
            _charge_user_for_match(
                user_id=user_id,
                match=match,
                amount=SPARRING_DOUBLES_COST,
                reason=FancoinTransaction.Reason(
                    FancoinTransaction.Reason.SPARRING_DOUBLES
                ),
            )
        return

    single_ids = {
        player.user_id
        for player in (match.player1, match.player2)
        if player is not None
    }
    for user_id in sorted(single_ids):
        _charge_user_for_match(
            user_id=user_id,
            match=match,
            amount=SPARRING_SINGLES_COST,
            reason=FancoinTransaction.Reason(
                FancoinTransaction.Reason.SPARRING_SINGLES
            ),
        )
