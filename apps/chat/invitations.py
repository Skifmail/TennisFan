"""Подписанное подтверждение и ограниченная по объёму очередь приглашений."""

from dataclasses import dataclass
from functools import partial
from typing import Final
from uuid import UUID, uuid4

from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Max, Q, QuerySet
from django.urls import reverse
from django.utils import timezone
from loguru import logger

from apps.core.localities import primary_locality_name
from apps.notifications.services import (
    center_enabled,
    create_notification,
    event_policy,
    preferences,
)
from apps.users.context_processors import invalidate_unread_notifications_cache
from apps.users.models import User

from .domain import MAX_DATABASE_ID, ChatAccessError, ChatError, ChatValidationError
from .invitation_email import send_invitation_email
from .models import (
    ChatInvitation,
    ChatInvitationCampaign,
    ChatInvitationEmailBlock,
    ChatMembership,
    ChatRoom,
)
from .services import require_active_user, require_member

PREVIEW_SALT: Final = "chat.invitation.preview.v1"
EMAIL_SALT: Final = "chat.invitation.email-off.v1"
PREVIEW_MAX_AGE: Final = 900
SEARCH_LIMIT: Final = 20
SEARCH_MAX_LENGTH: Final = 100
USER_BATCH_SIZE: Final = 200
EMAIL_BATCH_SIZE: Final = 25
CAMPAIGNS_PER_TICK: Final = 5
MAX_BATCH_SIZE: Final = 1000
TOKEN_MAX_LENGTH: Final = 4096
AUDIENCES: Final = frozenset({"person", "city", "platform"})


@dataclass(frozen=True)
class InvitationPreview:
    """Содержит безопасное подтверждение и число доступных получателей.

    Attributes:
        token: Подписанные параметры без контактных данных.
        count: Количество получателей на момент просмотра.
        label: Название аудитории для подтверждения.
    """

    token: str
    count: int
    label: str


@dataclass(frozen=True)
class InvitationCandidate:
    """Передаёт в поиск только номер и имя игрока.

    Attributes:
        id: Номер аккаунта.
        label: Имя и фамилия без контактного fallback.
    """

    id: int
    label: str


def _require_inviter(user: User, room_id: int) -> ChatMembership:
    member = require_member(user=user, room_id=room_id, writing=True)
    if member.room.is_direct:
        raise ChatAccessError("В личный чат нельзя приглашать третьих участников.")
    if not (
        member.room.administrator_id == user.pk
        or member.is_moderator
        or (user.is_staff and user.has_perm("chat.change_chatroom"))
    ):
        raise ChatAccessError("Приглашать могут администратор и модераторы этого чата.")
    return member


def can_invite(*, user: User, room_id: int) -> bool:
    """Проверяет показ кнопки, не заменяя серверную проверку отправки.

    Args:
        user: Текущий игрок.
        room_id: Комната назначения.

    Returns:
        Доступна ли функция приглашений.
    """
    try:
        _require_inviter(user, room_id)
    except ChatError:
        return False
    return True


def _eligible(room_id: int) -> QuerySet[User]:
    return (
        User.objects.filter(is_active=True)
        .exclude(
            pk__in=ChatMembership.objects.filter(
                room_id=room_id, is_active=True
            ).values("user_id")
        )
        .exclude(
            pk__in=ChatInvitation.objects.filter(room_id=room_id).values("recipient_id")
        )
    )


def _same_city(user: User, room: ChatRoom) -> bool:
    player = getattr(user, "player", None)
    return (
        room.city is not None
        and primary_locality_name(getattr(player, "city", "")).casefold()
        == primary_locality_name(room.city.name).casefold()
    )


def search_invitees(
    *, user: User, room_id: int, query: str
) -> list[InvitationCandidate]:
    """Ищет подходящих игроков только по имени и фамилии.

    Args:
        user: Уполномоченный инициатор.
        room_id: Выбранный чат.
        query: Имя, фамилия либо их сочетание.

    Returns:
        Ограниченный список без email и телефона.

    Raises:
        ChatError: Нет прав или запрос слишком длинный.
    """
    _require_inviter(user, room_id)
    if len(query) > SEARCH_MAX_LENGTH:
        raise ChatValidationError("Сократите поисковый запрос до 100 символов.")
    if not query.strip():
        return []
    rows = _eligible(room_id)
    for part in query.split():
        condition = Q()
        for variant in {part, part.lower(), part.upper(), part.title()}:
            condition |= Q(first_name__icontains=variant) | Q(
                last_name__icontains=variant
            )
        rows = rows.filter(condition)
    return [
        InvitationCandidate(
            id=row.pk, label=row.get_full_name().strip() or f"Игрок #{row.pk}"
        )
        for row in rows.order_by("first_name", "last_name", "pk")[:SEARCH_LIMIT]
    ]


def preview_invitation(
    *, user: User, room_id: int, audience: str, target_id: int | None = None
) -> InvitationPreview:
    """Считает аудиторию и подписывает одноразовое подтверждение без отправки.

    Args:
        user: Администратор или модератор комнаты.
        room_id: Чат назначения.
        audience: person, city либо platform.
        target_id: Номер игрока для персонального приглашения.

    Returns:
        Число получателей и подписанная форма со сроком действия.

    Raises:
        ChatError: Нет доступа либо неверно выбрана аудитория.
    """
    member = _require_inviter(user, room_id)
    if audience not in AUDIENCES or (
        audience == "person"
        and (target_id is None or not 0 < target_id <= MAX_DATABASE_ID)
    ):
        raise ChatValidationError("Выберите игрока или способ приглашения.")
    if audience == "city" and not member.room.city_id:
        raise ChatValidationError(
            "У отдельного чата нет города для такого приглашения."
        )
    upper = User.objects.aggregate(value=Max("pk"))["value"] or 0
    rows = _eligible(room_id).filter(pk__lte=upper)
    if audience == "person":
        rows = rows.filter(pk=target_id) if target_id is not None else rows.none()
    count = (
        sum(
            _same_city(row, member.room)
            for row in rows.select_related("player").iterator(
                chunk_size=USER_BATCH_SIZE
            )
        )
        if audience == "city"
        else rows.count()
    )
    payload = {
        "actor": user.pk,
        "room": room_id,
        "audience": audience,
        "target": target_id if audience == "person" else None,
        "upper": upper,
        "count": count,
        "nonce": str(uuid4()),
    }
    label = {
        "person": "Выбранный игрок",
        "city": f"Игроки города «{member.room.display_name}»",
        "platform": "Все пользователи платформы вне этого чата",
    }[audience]
    if audience == "person" and target_id is not None:
        recipient = rows.first()
        if recipient:
            label = f"Игрок: {recipient.get_full_name().strip() or 'Участник'} (#{recipient.pk})"
    return InvitationPreview(
        token=signing.dumps(payload, salt=PREVIEW_SALT), count=count, label=label
    )


@transaction.atomic
def queue_invitation(*, user: User, room_id: int, token: str) -> ChatInvitationCampaign:
    """Подтверждает подписанную аудиторию и ставит задачу, не отправляя SMTP в запросе.

    Args:
        user: Автор подписанного предпросмотра.
        room_id: Та же комната.
        token: Подтверждение с ограниченным сроком действия.

    Returns:
        Новая либо уже созданная по этому подтверждению отправка.

    Raises:
        ChatError: Нет доступа, подпись неверна, истекла или получателей нет.
    """
    if len(token) > TOKEN_MAX_LENGTH:
        raise ChatValidationError("Подтверждение недействительно.")
    try:
        payload = signing.loads(token, salt=PREVIEW_SALT, max_age=PREVIEW_MAX_AGE)
        nonce = UUID(payload["nonce"])
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ChatValidationError(
            "Подтверждение устарело. Проверьте получателей заново."
        ) from exc
    ChatRoom.objects.select_for_update().filter(pk=room_id).first()
    _require_inviter(user, room_id)
    if (
        payload["actor"] != user.pk
        or payload["room"] != room_id
        or payload["audience"] not in AUDIENCES
    ):
        raise ChatValidationError(
            "Подтверждение относится к другому пользователю или чату."
        )
    existing = ChatInvitationCampaign.objects.filter(pk=nonce).first()
    if existing:
        return existing
    if payload["count"] <= 0:
        raise ChatValidationError(
            "Новых получателей нет: игроки уже участвуют или приглашены."
        )
    campaign = ChatInvitationCampaign.objects.create(
        id=nonce,
        room_id=room_id,
        sender=user,
        audience=payload["audience"],
        target_id=payload["target"],
        upper_user_id=payload["upper"],
        estimated_count=payload["count"],
    )
    logger.info(
        "Приглашения поставлены в очередь",
        room_id=room_id,
        sender_id=user.pk,
        campaign_id=str(nonce),
        recipients=payload["count"],
    )
    return campaign


def _campaign_allowed(campaign: ChatInvitationCampaign) -> bool:
    if campaign.sender is None:
        return False
    return can_invite(user=campaign.sender, room_id=campaign.room_id)


@transaction.atomic
def _expand_campaign(batch_size: int) -> int:
    campaign = (
        ChatInvitationCampaign.objects.select_for_update(skip_locked=True, of=("self",))
        .filter(status="queued")
        .select_related("room__city", "sender")
        .order_by("created_at", "pk")
        .first()
    )
    if campaign is None:
        return 0
    ChatRoom.objects.select_for_update().filter(pk=campaign.room_id).first()
    if not _campaign_allowed(campaign):
        campaign.status = "cancelled"
        campaign.finished_at = timezone.now()
        campaign.save(update_fields=["status", "finished_at"])
        return 0
    rows = _eligible(campaign.room_id).filter(
        pk__gt=campaign.cursor_user_id, pk__lte=campaign.upper_user_id
    )
    if campaign.audience == "person":
        rows = (
            rows.filter(pk=campaign.target_id)
            if campaign.target_id is not None
            else rows.none()
        )
    users = list(rows.select_related("player").order_by("pk")[:batch_size])
    made = 0
    for recipient in users:
        campaign.cursor_user_id = recipient.pk
        if campaign.audience == "city" and not _same_city(recipient, campaign.room):
            continue
        invitation, created = ChatInvitation.objects.get_or_create(
            room_id=campaign.room_id,
            recipient=recipient,
            defaults={"campaign": campaign},
        )
        if not created:
            continue
        notification = create_notification(
            user=recipient,
            message=f"Вас приглашают в городской чат «{campaign.room.display_name}»"[
                :255
            ],
            url=reverse("chat:invitation", args=[invitation.pk]),
            event_key="chat.invited",
        )
        invitation.notification = notification
        invitation.save(update_fields=["notification"])
        transaction.on_commit(
            partial(invalidate_unread_notifications_cache, recipient.pk)
        )
        made += 1
    campaign.created_count += made
    if len(users) < batch_size:
        campaign.status = "done"
        campaign.finished_at = timezone.now()
    campaign.save(
        update_fields=["cursor_user_id", "created_count", "status", "finished_at"]
    )
    return made


@transaction.atomic
def _claim_email() -> ChatInvitation | None:
    invitation = (
        ChatInvitation.objects.select_for_update(skip_locked=True, of=("self",))
        .filter(email_status="pending")
        .select_related("room__city", "recipient", "campaign__sender")
        .order_by("created_at", "pk")
        .first()
    )
    if invitation is None:
        return None
    reason = ""
    if not _campaign_allowed(invitation.campaign):
        reason = "sender_access_lost"
    elif not invitation.recipient.is_active or invitation.status != "pending":
        reason = "recipient_unavailable"
    elif ChatMembership.objects.filter(
        room=invitation.room, user=invitation.recipient, is_active=True
    ).exists():
        reason = "already_joined"
        invitation.status = "accepted"
    elif center_enabled() and (
        not event_policy("chat.invited").email_enabled
        or not preferences(invitation.recipient_id)[1]
    ):
        reason = "notification_email_disabled"
    elif not invitation.recipient.email_verified:
        reason = "email_unverified"
    elif ChatInvitationEmailBlock.objects.filter(user=invitation.recipient).exists():
        reason = "email_unsubscribed"
    else:
        try:
            validate_email(invitation.recipient.email)
        except ValidationError:
            reason = "email_invalid"
    invitation.email_status = "skipped" if reason else "sending"
    invitation.email_reason = reason
    invitation.email_attempted_at = timezone.now()
    invitation.save(
        update_fields=["email_status", "email_reason", "email_attempted_at", "status"]
    )
    return invitation


def process_invitation_queue(
    *, user_batch_size: int = USER_BATCH_SIZE, email_limit: int = EMAIL_BATCH_SIZE
) -> None:
    """Обрабатывает ограниченные пакеты, резервируя SMTP-попытку до отправки.

    После сбоя процесса состояние sending не повторяется автоматически: невозможно
    гарантировать, что SMTP не принял письмо перед обрывом соединения.

    Args:
        user_batch_size: Максимум проверяемых аккаунтов за одну порцию.
        email_limit: Максимум разбираемых писем за вызов.

    Raises:
        ChatValidationError: Размер пакета выходит за безопасные границы.
    """
    if (
        isinstance(user_batch_size, bool)
        or isinstance(email_limit, bool)
        or not 0 < user_batch_size <= MAX_BATCH_SIZE
        or not 0 <= email_limit <= MAX_BATCH_SIZE
    ):
        raise ChatValidationError("Неверный размер пакета приглашений.")
    for _ in range(CAMPAIGNS_PER_TICK):
        _expand_campaign(user_batch_size)
    for _ in range(email_limit):
        invitation = _claim_email()
        if invitation is None:
            break
        if invitation.email_status != "sending":
            continue
        try:
            success = send_invitation_email(invitation)
        except (OSError, TimeoutError):
            logger.exception(
                "Ошибка почтового транспорта приглашения",
                invitation_id=str(invitation.pk),
            )
            success = False
        ChatInvitation.objects.filter(pk=invitation.pk, email_status="sending").update(
            email_status="sent" if success else "failed",
            email_reason="" if success else "transport_failure",
            email_sent_at=timezone.now() if success else None,
        )
        logger.info(
            "Обработано email-приглашение",
            invitation_id=str(invitation.pk),
            sent=success,
        )


def invitation_email_token(*, user_id: int) -> str:
    """Формирует ссылку только для отключения этого типа писем, не для авторизации.

    Args:
        user_id: Владелец адреса.

    Returns:
        Подписанное разрешение отписаться без раскрытия email.
    """
    return signing.dumps({"user": user_id}, salt=EMAIL_SALT)


def unsubscribe_invitation_email(*, token: str) -> None:
    """Отключает будущие email-приглашения после явного POST-подтверждения.

    Args:
        token: Подпись из письма.

    Raises:
        ChatValidationError: Ссылка повреждена либо аккаунта уже нет.
    """
    try:
        if len(token) > TOKEN_MAX_LENGTH:
            raise signing.BadSignature
        payload = signing.loads(token, salt=EMAIL_SALT)
        user_id = payload["user"]
        if not isinstance(user_id, int) or not 0 < user_id <= MAX_DATABASE_ID:
            raise signing.BadSignature
    except (signing.BadSignature, KeyError, TypeError) as exc:
        raise ChatValidationError("Ссылка отключения писем недействительна.") from exc
    if not User.objects.filter(pk=user_id).exists():
        raise ChatValidationError("Аккаунт больше не существует.")
    ChatInvitationEmailBlock.objects.get_or_create(user_id=user_id)


@transaction.atomic
def decline_invitation(*, user: User, invitation_id: UUID) -> None:
    """Фиксирует отказ только владельца приглашения, не изменяя участие.

    Args:
        user: Получатель.
        invitation_id: Его приглашение.

    Raises:
        ChatAccessError: Аккаунт неактивен либо приглашение чужое.
    """
    require_active_user(user)
    invitation = (
        ChatInvitation.objects.select_for_update()
        .filter(pk=invitation_id, recipient=user)
        .first()
    )
    if invitation is None:
        raise ChatAccessError("Приглашение недоступно.")
    if invitation.status == "pending":
        invitation.status = "declined"
        invitation.answered_at = timezone.now()
        invitation.save(update_fields=["status", "answered_at"])
