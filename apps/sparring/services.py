"""
Сервисы для работы со спаррингами.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from loguru import logger

from apps.notifications.services import create_notification
from apps.tournaments.models import Match

from .models import SparringInvitation, SparringRequest, SparringResponse

if TYPE_CHECKING:
    from apps.users.models import Player


def _deadline_for_sparring_invitation(invitation: SparringInvitation) -> datetime:
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
    from apps.users.onboarding import ensure_tennis_participants

    ensure_tennis_participants((invitation.inviter, invitation.invitee))
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
        "Создан спарринг {} из приглашения {} (игроки {} и {})",
        match.pk,
        invitation.pk,
        inviter.pk,
        invitee.pk,
    )

    return match


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
    from apps.telegram_bot.notifications import (
        notify_sparring_invitation_accepted_inviter,
    )

    from .utils import user_has_sparring_access

    inv = (
        SparringInvitation.objects.select_for_update().filter(pk=invitation_id).first()
    )
    if inv is None:
        raise ValueError("Приглашение не найдено.")
    if inv.invitee.user_id != acting_user_id:
        raise ValueError("Принять приглашение может только приглашённый игрок.")
    if not user_has_sparring_access(inv.invitee.user):
        raise ValueError("Нет доступа к спаррингам по действующему тарифу.")
    if inv.status != SparringInvitation.Status.PENDING or inv.match_id is not None:
        raise ValueError("Приглашение уже обработано.")

    match = create_match_from_invitation(inv)
    inv.status = SparringInvitation.Status.ACCEPTED
    inv.match = match
    inv.save(update_fields=["status", "match", "updated_at"])
    notify_sparring_invitation_accepted_inviter(inv, match)
    return match


@transaction.atomic
def reject_sparring_invitation(invitation_id: int, acting_user_id: int) -> None:
    """
    Отклонить приглашение (только приглашённый).

    Args:
        invitation_id: ID приглашения.
        acting_user_id: ID пользователя.

    Raises:
        ValueError: Нет прав или приглашение не в статусе «ожидает».
    """
    inv = SparringInvitation.objects.select_for_update().get(pk=invitation_id)
    if inv.invitee.user_id != acting_user_id:
        raise ValueError("Отклонить может только приглашённый игрок.")
    if inv.status != SparringInvitation.Status.PENDING or inv.match_id is not None:
        raise ValueError("Приглашение уже обработано.")
    inv.status = SparringInvitation.Status.REJECTED
    inv.save(update_fields=["status", "updated_at"])


@transaction.atomic
def cancel_sparring_invitation(invitation_id: int, acting_user_id: int) -> None:
    """
    Отменить исходящее приглашение (только пригласивший).

    Args:
        invitation_id: ID приглашения.
        acting_user_id: ID пользователя.

    Raises:
        ValueError: Нет прав или приглашение не в статусе «ожидает».
    """
    inv = SparringInvitation.objects.select_for_update().get(pk=invitation_id)
    if inv.inviter.user_id != acting_user_id:
        raise ValueError("Отменить может только автор приглашения.")
    if inv.status != SparringInvitation.Status.PENDING or inv.match_id is not None:
        raise ValueError("Приглашение уже обработано.")
    inv.status = SparringInvitation.Status.CANCELLED
    inv.save(update_fields=["status", "updated_at"])


def create_match_from_response(sparring_response: SparringResponse) -> Match:
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
    from apps.users.onboarding import ensure_tennis_participants

    ensure_tennis_participants(
        (request.player, sparring_response.respondent), request.sport
    )
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
        sport=request.sport,
    )

    logger.info(
        "Создан спарринг {} из отклика {} (заявка {})",
        match.pk,
        sparring_response.pk,
        request.pk,
    )

    return match


def _lock_sparring_request(request_id: int) -> SparringRequest:
    """Взять общую блокировку заявки до блокировок её откликов."""
    request = SparringRequest.objects.select_for_update().filter(pk=request_id).first()
    if request is None:
        raise ValueError("Заявка не найдена.")
    return request


@transaction.atomic
def respond_to_sparring(
    request_id: int, respondent: Player, *, contact_method: str
) -> tuple[SparringResponse, bool]:
    """Сохранить отклик и уведомления, не сбрасывая принятый отклик после открытия заявки."""
    from apps.telegram_bot.notifications import notify_sparring_response
    from apps.users.onboarding import ensure_tennis_participants

    from .utils import user_has_sparring_access

    request = _lock_sparring_request(request_id)
    if request.status != SparringRequest.Status.ACTIVE:
        raise ValueError("Заявка уже закрыта.")
    if request.player_id == respondent.pk:
        raise ValueError("Нельзя откликнуться на свою заявку.")
    if contact_method not in SparringResponse.ContactMethod.values:
        raise ValueError("Укажите допустимый способ связи.")
    if not user_has_sparring_access(respondent.user):
        raise ValueError("Нет доступа к спаррингам по действующему тарифу.")
    ensure_tennis_participants((request.player, respondent), request.sport)
    response = (
        SparringResponse.objects.select_for_update()
        .filter(sparring_request=request, respondent=respondent)
        .first()
    )
    created = response is None
    if response is not None:
        if (
            response.status == SparringResponse.ResponseStatus.ACCEPTED
            or response.matches.exists()
        ):
            raise ValueError("Ваш отклик уже принят. По нему создан матч.")
        response.contact_method = contact_method
        response.status = SparringResponse.ResponseStatus.PENDING
        response.save(update_fields=["contact_method", "status", "updated_at"])
    else:
        response = SparringResponse.objects.create(
            sparring_request=request,
            respondent=respondent,
            contact_method=contact_method,
        )
        create_notification(
            user=request.player.user,
            message=f"{respondent} откликнулся на вашу заявку на спарринг.",
            url=reverse("sparring_my_requests"),
            event_key="sparring.response",
        )
        notify_sparring_response(response)
    return response, created


@transaction.atomic
def confirm_sparring_response(response_id: int, acting_user_id: int) -> Match:
    """Однократно принять свежий отклик, создать матч и закрыть заявку вместе."""
    from apps.telegram_bot.notifications import notify_sparring_response_accepted

    from .utils import user_has_sparring_access

    request_id = (
        SparringResponse.objects.filter(pk=response_id)
        .values_list("sparring_request_id", flat=True)
        .first()
    )
    if request_id is None:
        raise ValueError("Отклик не найден.")
    request = _lock_sparring_request(request_id)
    response = (
        SparringResponse.objects.select_for_update()
        .filter(pk=response_id, sparring_request=request)
        .first()
    )
    if response is None:
        raise ValueError("Отклик не найден.")
    if request.player.user_id != acting_user_id:
        raise ValueError("Подтвердить отклик может только автор заявки.")
    if not user_has_sparring_access(request.player.user):
        raise ValueError("Нет доступа к спаррингам по действующему тарифу.")
    if request.status != SparringRequest.Status.ACTIVE:
        raise ValueError("Заявка уже закрыта.")
    if (
        response.status != SparringResponse.ResponseStatus.PENDING
        or response.matches.exists()
    ):
        raise ValueError("Этот отклик уже обработан.")
    response.sparring_request = request
    match = create_match_from_response(response)
    response.status = SparringResponse.ResponseStatus.ACCEPTED
    response.save(update_fields=["status", "updated_at"])
    request.status = SparringRequest.Status.CLOSED
    request.save(update_fields=["status", "updated_at"])
    request.responses.filter(status=SparringResponse.ResponseStatus.PENDING).exclude(
        pk=response.pk
    ).update(status=SparringResponse.ResponseStatus.REJECTED)
    deadline = (
        timezone.localtime(match.deadline).strftime("%d.%m.%Y")
        if match.deadline
        else "не установлен"
    )
    create_notification(
        user=response.respondent.user,
        message=f"Ваш отклик на заявку на спарринг принят! Матч создан. Дедлайн: {deadline}.",
        url=reverse("match_detail", args=[match.pk]),
        event_key="sparring.accepted",
    )
    notify_sparring_response_accepted(response, match)
    return match


@transaction.atomic
def cancel_sparring_response(response_id: int, acting_user_id: int) -> None:
    """Отменить только свежий ожидающий отклик, не затрагивая уже созданный матч."""
    request_id = (
        SparringResponse.objects.filter(pk=response_id)
        .values_list("sparring_request_id", flat=True)
        .first()
    )
    if request_id is None:
        raise ValueError("Отклик не найден.")
    request = _lock_sparring_request(request_id)
    response = (
        SparringResponse.objects.select_for_update()
        .filter(pk=response_id, sparring_request=request)
        .first()
    )
    if response is None:
        raise ValueError("Отклик не найден.")
    if response.respondent.user_id != acting_user_id:
        raise ValueError("Можно отменить только свой отклик.")
    if (
        request.status != SparringRequest.Status.ACTIVE
        or response.status != SparringResponse.ResponseStatus.PENDING
        or response.matches.exists()
    ):
        raise ValueError("Отклик уже обработан.")
    response.status = SparringResponse.ResponseStatus.REJECTED
    response.save(update_fields=["status", "updated_at"])


@transaction.atomic
def cancel_sparring_request(request_id: int, acting_user_id: int) -> bool:
    """Закрыть свою заявку и ожидающие отклики, если по ней ещё нет матча."""
    request = _lock_sparring_request(request_id)
    if request.player.user_id != acting_user_id:
        raise ValueError("Можно отменить только свою заявку.")
    if Match.objects.filter(sparring_response__sparring_request=request).exists():
        raise ValueError("По этой заявке уже создан матч. Отменить её нельзя.")
    if request.status != SparringRequest.Status.ACTIVE:
        return False
    request.status = SparringRequest.Status.CLOSED
    request.save(update_fields=["status", "updated_at"])
    request.responses.filter(status=SparringResponse.ResponseStatus.PENDING).update(
        status=SparringResponse.ResponseStatus.REJECTED
    )
    return True


@transaction.atomic
def delete_sparring_request(request_id: int, acting_user_id: int) -> None:
    """Удалить свою заявку без потери контекста уже созданного матча."""
    request = _lock_sparring_request(request_id)
    if request.player.user_id != acting_user_id:
        raise ValueError("Можно удалить только свою заявку.")
    if Match.objects.filter(sparring_response__sparring_request=request).exists():
        raise ValueError("По этой заявке уже создан матч. Удаление недоступно.")
    request.delete()
