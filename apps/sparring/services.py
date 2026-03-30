"""
Сервисы для работы со спаррингами.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from apps.tournaments.models import Match

from .models import SparringInvitation

logger = logging.getLogger(__name__)


def _deadline_for_sparring_invitation(invitation: SparringInvitation):
    """Вычислить дедлайн матча по приглашению (дата игры + 7 дней на внесение результата или +7 дней от сейчас)."""
    if invitation.proposed_date:
        dt_end = timezone.make_aware(
            datetime.combine(invitation.proposed_date, time(23, 59, 59)),
            timezone.get_current_timezone(),
        )
        return dt_end + timedelta(days=7)
    return timezone.now() + timedelta(days=7)


def create_match_from_invitation(invitation: SparringInvitation) -> Match:
    """
    Создаёт матч 1×1 из принятого приглашения на спарринг.

    Args:
        invitation: Объект приглашения (inviter / invitee, is_friendly).

    Returns:
        Созданный матч.

    Raises:
        ValueError: Если участники совпадают или данные некорректны.
    """
    inviter = invitation.inviter
    invitee = invitation.invitee
    if inviter.id == invitee.id:
        raise ValueError("Нельзя создать матч с самим собой")

    rating_status = (
        Match.RatingCalcStatus.NOT_APPLICABLE
        if invitation.is_friendly
        else Match.RatingCalcStatus.PENDING
    )
    deadline = _deadline_for_sparring_invitation(invitation)

    match = Match.objects.create(
        tournament=None,
        match_type=Match.MatchType.SPARRING,
        sparring_response=None,
        player1=inviter,
        player2=invitee,
        status=Match.MatchStatus.SCHEDULED,
        deadline=deadline,
        rating_status=rating_status,
    )

    logger.info(
        "Created sparring match %s from invitation %s (inviter=%s invitee=%s)",
        match.pk,
        invitation.pk,
        inviter.pk,
        invitee.pk,
    )

    return match  # type: ignore[no-any-return]


@transaction.atomic
def accept_sparring_invitation(invitation_id: int, acting_user_id: int) -> Match:
    """
    Принять приглашение на спарринг (только приглашённый пользователь).

    Args:
        invitation_id: ID приглашения.
        acting_user_id: ID пользователя, выполняющего действие.

    Returns:
        Созданный матч.

    Raises:
        ValueError: Нет прав, неверный статус или ошибка создания матча.
    """
    inv = (
        SparringInvitation.objects.select_for_update()
        .select_related("inviter", "invitee", "invitee__user", "inviter__user")
        .get(pk=invitation_id)
    )
    if inv.invitee.user_id != acting_user_id:
        raise ValueError("Принять приглашение может только приглашённый игрок.")
    if inv.status != SparringInvitation.Status.PENDING:
        raise ValueError("Приглашение уже обработано.")

    match = create_match_from_invitation(inv)
    inv.status = SparringInvitation.Status.ACCEPTED
    inv.match = match
    inv.save(update_fields=["status", "match", "updated_at"])
    return match


def reject_sparring_invitation(invitation_id: int, acting_user_id: int) -> None:
    """
    Отклонить приглашение (только приглашённый).

    Args:
        invitation_id: ID приглашения.
        acting_user_id: ID пользователя.

    Raises:
        ValueError: Нет прав или приглашение не в статусе «ожидает».
    """
    inv = SparringInvitation.objects.select_related("invitee__user").get(
        pk=invitation_id
    )
    if inv.invitee.user_id != acting_user_id:
        raise ValueError("Отклонить может только приглашённый игрок.")
    if inv.status != SparringInvitation.Status.PENDING:
        raise ValueError("Приглашение уже обработано.")
    inv.status = SparringInvitation.Status.REJECTED
    inv.save(update_fields=["status", "updated_at"])


def cancel_sparring_invitation(invitation_id: int, acting_user_id: int) -> None:
    """
    Отменить исходящее приглашение (только пригласивший).

    Args:
        invitation_id: ID приглашения.
        acting_user_id: ID пользователя.

    Raises:
        ValueError: Нет прав или приглашение не в статусе «ожидает».
    """
    inv = SparringInvitation.objects.select_related("inviter__user").get(
        pk=invitation_id
    )
    if inv.inviter.user_id != acting_user_id:
        raise ValueError("Отменить может только автор приглашения.")
    if inv.status != SparringInvitation.Status.PENDING:
        raise ValueError("Приглашение уже обработано.")
    inv.status = SparringInvitation.Status.CANCELLED
    inv.save(update_fields=["status", "updated_at"])


def create_match_from_response(sparring_response) -> Match:
    """
    Создает матч из отклика на спарринг.

    Args:
        sparring_response: SparringResponse объект

    Returns:
        Созданный Match объект

    Raises:
        ValueError: Если данные некорректны
    """
    request = sparring_response.sparring_request
    author = request.player
    respondent = sparring_response.respondent

    if author.id == respondent.id:
        raise ValueError("Нельзя создать матч с самим собой")

    # Дружеский матч не влияет на рейтинг и силу
    rating_status = (
        Match.RatingCalcStatus.NOT_APPLICABLE
        if request.is_friendly
        else Match.RatingCalcStatus.PENDING
    )

    match = Match.objects.create(
        tournament=None,
        match_type=Match.MatchType.SPARRING,
        sparring_response=sparring_response,
        player1=author,
        player2=respondent,
        status=Match.MatchStatus.SCHEDULED,
        deadline=timezone.now() + timedelta(days=7),
        rating_status=rating_status,
    )

    logger.info(
        "Created sparring match %s from response %s (request %s)",
        match.pk,
        sparring_response.pk,
        request.pk,
    )

    return match  # type: ignore[no-any-return]
