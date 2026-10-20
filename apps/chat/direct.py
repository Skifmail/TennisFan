"""Личная переписка фиксированной пары и уведомления без содержания сообщений."""

from typing import cast

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from loguru import logger

from apps.notifications.services import create_notification
from apps.users.models import Player, User

from .domain import ChatAccessError, ChatError, ChatNotFoundError, ChatValidationError
from .models import ChatMembership, ChatMessage, ChatRoom


def require_direct_participant(*, user: User, room: ChatRoom) -> None:
    """Защищает состав личного чата независимо от записей участия и статуса сотрудника.

    Args:
        user: Авторизованный инициатор.
        room: Комната проверяемого действия.

    Raises:
        ChatAccessError: Аккаунт не является одним из двух собеседников.
    """
    if room.is_direct and user.pk not in {
        room.direct_user_low_id,
        room.direct_user_high_id,
    }:
        raise ChatAccessError("Эта личная переписка вам недоступна.")


def peer_for(*, user: User, room: ChatRoom) -> User | None:
    """Возвращает другого собеседника только после проверки принадлежности паре.

    Args:
        user: Текущий участник.
        room: Городская либо личная комната.

    Returns:
        Собеседник личной комнаты либо None для общего чата.

    Raises:
        ChatAccessError: Посторонний пытается узнать состав личного чата.
    """
    require_direct_participant(user=user, room=room)
    if not room.is_direct:
        return None
    return cast(
        User,
        (
            room.direct_user_high
            if room.direct_user_low_id == user.pk
            else room.direct_user_low
        ),
    )


@transaction.atomic
def open_direct(*, user: User, recipient_id: int) -> ChatRoom:
    """Открывает единственный диалог пары без выдачи согласий за собеседников.

    Args:
        user: Инициатор.
        recipient_id: Аккаунт выбранного игрока.

    Returns:
        Созданная либо существующая личная комната.

    Raises:
        ChatAccessError: Неактивный аккаунт.
        ChatValidationError: Попытка написать самому себе.
        ChatNotFoundError: Получатель недоступен.
    """
    from .services import require_active_user

    require_active_user(user)
    if recipient_id == user.pk:
        raise ChatValidationError("Нельзя создать переписку с собой.")
    if not User.objects.filter(pk=recipient_id, is_active=True).exists():
        raise ChatNotFoundError("Игрок недоступен.")
    low, high = sorted((user.pk, recipient_id))
    room, created = ChatRoom.objects.get_or_create(
        direct_user_low_id=low, direct_user_high_id=high
    )
    if not room.is_active:
        raise ChatAccessError("Диалог закрыт.")
    for account_id in (low, high):
        member, _ = ChatMembership.objects.get_or_create(room=room, user_id=account_id)
        # Только сам игрок может вернуться после выхода; чужой отзыв не отменяется.
        if account_id == user.pk and not member.is_active:
            member.is_active = True
            member.save(update_fields=["is_active"])
    if created:
        logger.info("Личный диалог создан", room_id=room.pk, actor_id=user.pk)
    return cast(ChatRoom, room)


def notify_direct_message(message: ChatMessage) -> None:
    """Создаёт одну запись ЛК и штатное push-задание без текста переписки.

    Args:
        message: Впервые сохранённое сообщение, не повтор идемпотентного запроса.
    """
    room = message.room
    recipient_id = (
        room.direct_user_high_id
        if message.author_id == room.direct_user_low_id
        else room.direct_user_low_id
    )
    if not room.is_direct or recipient_id is None:
        return
    if ChatMembership.objects.filter(
        room=room,
        user_id=recipient_id,
        user__is_active=True,
        is_active=True,
        notifications_enabled=True,
    ).exists():
        create_notification(
            event_key="chat.direct_message",
            user_id=recipient_id,
            message="У вас новое сообщение в личном чате.",
            url=reverse("chat:room", args=[room.pk]),
        )


@login_required
@require_POST
def start(request: HttpRequest, player_id: int) -> HttpResponse:
    """Принимает защищённую кнопку из профиля или заявки на спарринг.

    Args:
        request: Авторизованный POST с CSRF.
        player_id: Профиль получателя, а не произвольный номер комнаты.

    Returns:
        Переход к диалогу либо безопасный отказ.
    """
    player = get_object_or_404(Player, pk=player_id, is_bye=False, user__is_active=True)
    try:
        room = open_direct(user=cast(User, request.user), recipient_id=player.user_id)
    except ChatError as exc:
        return HttpResponse(str(exc), status=exc.status)
    return redirect("chat:room", room_id=room.pk)
