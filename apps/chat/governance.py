"""Управление ролями одного сообщества без доступа к данным профилей."""

from django.db import transaction
from django.utils import timezone
from loguru import logger

from apps.users.models import User

from .consent import ensure_chat_consent, publish_disclosure_change
from .domain import ChatAccessError, ChatNotFoundError, ChatValidationError
from .models import ChatManagementEvent, ChatMembership, ChatRoom


def _platform_manager(user: User) -> bool:
    return bool(user.is_staff and user.has_perm("chat.change_chatroom"))


def can_manage(*, user: User, room: ChatRoom) -> bool:
    """Проверяет полномочия владельца или уполномоченного сотрудника платформы.

    Args:
        user: Инициатор управления.
        room: Выбранное сообщество.

    Returns:
        Есть ли право менять управляющих именно в этой комнате.
    """
    return bool(
        not room.is_direct
        and user.is_active
        and user.is_authenticated
        and (room.administrator_id == user.pk or _platform_manager(user))
    )


@transaction.atomic
def change_role(
    *, user: User, room_id: int, target_id: int, operation: str, enabled: bool = False
) -> None:
    """Назначает модератора либо передаёт единоличное управление участнику.

    Args:
        user: Администратор комнаты либо уполномоченный сотрудник.
        room_id: Комната изменения.
        target_id: Действующий участник с согласиями.
        operation: moderator либо transfer.
        enabled: Выдаётся ли роль модератора.

    Raises:
        ChatAccessError: Недостаточно прав или цель недопустима.
        ChatNotFoundError: Сообщество отсутствует.
        ChatValidationError: Неизвестное действие.
    """
    room = (
        ChatRoom.objects.select_for_update().filter(pk=room_id, is_created=True).first()
    )
    if room is None:
        raise ChatNotFoundError("Чат не найден.")
    if not can_manage(user=user, room=room):
        raise ChatAccessError("Требуются права администратора этого чата.")
    if not _platform_manager(user):
        actor = ChatMembership.objects.filter(
            room=room, user=user, is_active=True
        ).first()
        if actor is None or not room.is_active:
            raise ChatAccessError("Сначала вступите в чат.")
        ensure_chat_consent(actor)
    target = (
        ChatMembership.objects.select_related("user")
        .filter(room=room, user_id=target_id, is_active=True, user__is_active=True)
        .first()
    )
    if target is None:
        raise ChatAccessError("Выберите действующего участника этого чата.")
    ensure_chat_consent(target)
    if target.banned_until and target.banned_until > timezone.now():
        raise ChatAccessError(
            "Нельзя назначить участника с ограничением отправки сообщений."
        )
    if operation == "transfer":
        if room.administrator_id == target_id:
            return
        room.administrator_id = target_id
        room.save(update_fields=["administrator"])
        action = "assigned" if _platform_manager(user) else "transferred"
    elif operation == "moderator":
        if target_id == room.administrator_id:
            raise ChatAccessError("Администратор уже имеет права модерации.")
        if target.is_moderator == enabled:
            return
        target.is_moderator = enabled
        target.save(update_fields=["is_moderator"])
        action = "moderator_added" if enabled else "moderator_removed"
    else:
        raise ChatValidationError("Неизвестное действие управления.")
    ChatManagementEvent.objects.create(
        room=room, actor=user, target=target.user, action=action
    )
    publish_disclosure_change(room_id=room.pk, user_id=target_id)
    logger.info(
        "Изменены права управления городским чатом",
        room_id=room.pk,
        actor_id=user.pk,
        target_id=target_id,
        action=action,
    )


def release_administrator(*, room: ChatRoom, user: User) -> None:
    """Освобождает управление при отзыве согласий внутри транзакции комнаты.

    Args:
        room: Уже заблокированная транзакцией комната.
        user: Отзывающий согласия игрок.
    """
    if room.administrator_id == user.pk:
        room.administrator = None
        room.save(update_fields=["administrator"])
        ChatManagementEvent.objects.create(
            room=room, actor=user, target=user, action="released"
        )
        logger.warning(
            "Городской чат остался без администратора", room_id=room.pk, user_id=user.pk
        )
