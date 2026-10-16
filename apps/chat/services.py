"""Сценарии городского чата с серверной авторизацией и журналом изменений."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final, TypedDict
from uuid import UUID

from django.db import transaction
from django.db.models import Count, F, IntegerField, OuterRef, Q, QuerySet, Subquery
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone
from loguru import logger

from apps.core.models import City
from apps.users.models import Notification, User

from .archive import ensure_message_snapshot, record_message_change
from .consent import ensure_chat_consent
from .domain import (
    EDIT_WINDOW_SECONDS,
    MAX_DATABASE_ID,
    MAX_PAGE_SIZE,
    MAX_REPORT_LENGTH,
    MESSAGE_INTERVAL_SECONDS,
    PAGE_SIZE,
    ChatAccessError,
    ChatNotFoundError,
    ChatRateLimitError,
    ChatValidationError,
    normalize_message,
)
from .governance import can_manage
from .models import ChatEvent, ChatMembership, ChatMessage, ChatReport, ChatRoom
from .realtime import publish_room_change

if TYPE_CHECKING:
    from django_stubs_ext import WithAnnotations


class _MembershipCounts(TypedDict):
    unread: int
    member_count: int


BAN_DURATION_DAYS: Final = 1


def require_active_user(user: User) -> None:
    """Проверяет активность аккаунта до обращения к комнате.

    Args:
        user: Текущий пользователь.

    Raises:
        ChatAccessError: Пользователь не вошёл или заблокирован.
    """
    if not user.is_authenticated or not user.is_active:
        raise ChatAccessError("Войдите в активный аккаунт.")


def _room(room_id: int, *, lock: bool = False) -> ChatRoom:
    if not 0 < room_id <= MAX_DATABASE_ID:
        raise ChatNotFoundError("Чат не найден.")
    query = ChatRoom.objects.select_related("city")
    if lock:
        query = query.select_for_update(of=("self",))
    room = query.filter(pk=room_id, is_active=True, is_created=True).first()
    if room is None:
        raise ChatNotFoundError("Чат не найден или закрыт.")
    return room


def require_member(
    *, user: User, room_id: int, writing: bool = False
) -> ChatMembership:
    """Проверяет актуальное участие и ограничение отправки.

    Args:
        user: Пользователь текущего запроса.
        room_id: Комната.
        writing: Требуется право отправлять сообщения.

    Returns:
        Действующее участие.

    Raises:
        ChatAccessError: При отсутствии участия, неактивном аккаунте или запрете писать.
        ChatNotFoundError: Идентификатор комнаты вне допустимого диапазона.
    """
    require_active_user(user)
    if not 0 < room_id <= MAX_DATABASE_ID:
        raise ChatNotFoundError("Чат не найден.")
    member = (
        ChatMembership.objects.select_related("room", "room__city")
        .filter(
            user=user,
            room_id=room_id,
            is_active=True,
            room__is_active=True,
            room__is_created=True,
        )
        .first()
    )
    if member is None:
        raise ChatAccessError("Сначала вступите в чат.")
    ensure_chat_consent(member)
    if writing and member.banned_until and member.banned_until > timezone.now():
        raise ChatAccessError("Отправка сообщений временно ограничена модератором.")
    return member


def _moderator(user: User, member: ChatMembership) -> bool:
    return bool(
        user.is_staff or member.is_moderator or member.room.administrator_id == user.pk
    )


def _message(room_id: int, message_id: int) -> ChatMessage:
    message = (
        ChatMessage.objects.select_related(
            "author", "author__player", "reply_to", "reply_to__author"
        )
        .filter(room_id=room_id, pk=message_id)
        .first()
    )
    if message is None:
        raise ChatNotFoundError("Сообщение не найдено.")
    return message


def _name(user: User | None) -> str:
    if user is None:
        return "Удалённый аккаунт"
    return f"{user.first_name} {user.last_name}".strip() or f"Игрок #{user.pk}"


def _moderator_ids(room_id: int) -> frozenset[int]:
    return frozenset(
        ChatMembership.objects.filter(
            Q(is_moderator=True)
            | Q(user__is_staff=True)
            | Q(user_id=F("room__administrator_id")),
            Q(disclosure_conditions="") | Q(disclosure_reviewed=True),
            consented_at__isnull=False,
            consent_withdrawn_at__isnull=True,
            room_id=room_id,
            room__is_active=True,
            is_active=True,
            user__is_active=True,
        ).values_list("user_id", flat=True)
    )


def serialize_message(
    message: ChatMessage,
    *,
    moderator_ids: frozenset[int] | None = None,
    disclosures: dict[int, ChatMembership] | None = None,
) -> dict[str, Any]:
    """Возвращает публичные данные без email, телефона и удалённого текста.

    Args:
        message: Сообщение с предварительно загруженным автором.
        moderator_ids: Актуальные модераторы комнаты для пакетной сериализации.
            Если не переданы, роли загружаются из базы.
        disclosures: Выбор данных авторами, загруженный одним запросом на страницу.

    Returns:
        Поля для безопасного отображения как обычного текста.
    """
    reply = message.reply_to
    if disclosures is None:
        disclosures = _message_disclosures(message.room_id, [message])
    author_member = disclosures.get(message.author_id or 0)
    permitted = _may_publish(author_member)
    player = getattr(message.author, "player", None)
    avatar = (
        player.avatar.url
        if permitted
        and author_member
        and author_member.disclosure_avatar
        and player
        and player.avatar
        else ""
    )
    reply_member = disclosures.get(reply.author_id or 0) if reply else None
    reply_permitted = _may_publish(reply_member)
    moderators = (
        _moderator_ids(message.room_id) if moderator_ids is None else moderator_ids
    )
    return {
        "id": message.pk,
        "revision": message.revision,
        "author_id": message.author_id,
        "author": _disclosed_name(message.author, author_member),
        "author_is_moderator": permitted and message.author_id in moderators,
        "author_is_administrator": bool(
            permitted
            and author_member
            and author_member.is_active
            and author_member.user.is_active
            and author_member.room.administrator_id == message.author_id
        ),
        "avatar": avatar,
        # Ссылка на полный профиль раскрывает больше данных, чем выбрано для чата.
        "profile_url": "",
        "conditions_url": reverse("chat:data_conditions", args=[message.room_id])
        + f"#participant-{message.author_id}",
        "text": "" if message.deleted_at or not permitted else message.text,
        "client_id": str(message.client_id),
        "created_at": message.created_at.isoformat(),
        "is_edited": message.edited_at is not None,
        "is_deleted": message.deleted_at is not None or not permitted,
        "reply_to": (
            {
                "id": reply.pk,
                "author": _disclosed_name(reply.author, reply_member),
                "text": (
                    "Сообщение недоступно"
                    if reply.deleted_at or not reply_permitted
                    else reply.text[:120]
                ),
            }
            if reply
            else None
        ),
    }


def _may_publish(member: ChatMembership | None) -> bool:
    return bool(
        member
        and member.consented_at
        and not member.consent_withdrawn_at
        and (not member.disclosure_conditions or member.disclosure_reviewed)
    )


def _disclosed_name(user: User | None, member: ChatMembership | None) -> str:
    if user is None:
        return "Удалённый аккаунт"
    return (
        _name(user)
        if _may_publish(member) and member and member.disclosure_name
        else f"Участник #{user.pk}"
    )


def _message_disclosures(
    room_id: int, messages: list[ChatMessage]
) -> dict[int, ChatMembership]:
    author_ids = {
        author_id
        for message in messages
        for author_id in (
            message.author_id,
            message.reply_to.author_id if message.reply_to else None,
        )
        if author_id is not None
    }
    return {
        member.user_id: member
        for member in ChatMembership.objects.select_related("user", "room").filter(
            room_id=room_id, user_id__in=author_ids
        )
    }


def _event(message: ChatMessage) -> None:
    event = ChatEvent.objects.create(room_id=message.room_id, message=message)
    message.revision = event.pk
    message.save(update_fields=["revision"])
    transaction.on_commit(lambda: publish_room_change(message.room_id))


@transaction.atomic
def join_city(*, user: User, city_id: int) -> ChatRoom:
    """Вступает в комнату любого выбранного населённого пункта.

    Args:
        user: Авторизованный участник.
        city_id: Запись справочника, не строка из профиля.

    Returns:
        Комната города.

    Raises:
        ChatAccessError: Аккаунт неактивен или комната закрыта.
        ChatNotFoundError: Населённый пункт отсутствует.
    """
    require_active_user(user)
    if not City.objects.filter(pk=city_id).exists():
        raise ChatNotFoundError("Населённый пункт не найден.")
    room = ChatRoom.objects.filter(city_id=city_id, is_created=True).first()
    if room is None:
        raise ChatNotFoundError("Чат ещё не создан. Сначала подтвердите его создание.")
    return join_room(user=user, room_id=room.pk)


@transaction.atomic
def join_room(*, user: User, room_id: int) -> ChatRoom:
    """Создаёт участие; личные согласия проверяются отдельным слоем доступа.

    Args:
        user: Авторизованный игрок.
        room_id: Городская либо отдельная комната.

    Returns:
        Открытая существующая комната.

    Raises:
        ChatAccessError: Аккаунт неактивен.
        ChatNotFoundError: Комната закрыта либо отсутствует.
    """
    require_active_user(user)
    room = _room(room_id, lock=True)
    member, created = ChatMembership.objects.get_or_create(
        room=room,
        user=user,
        defaults={
            "last_read_message_id": room.messages.order_by("-id")
            .values_list("id", flat=True)
            .first()
            or 0
        },
    )
    if not created and not member.is_active:
        member.is_active = True
        member.save(update_fields=["is_active"])
    logger.info(
        "Пользователь вступил в городской чат", room_id=room.pk, user_id=user.pk
    )
    return room


def leave_room(*, user: User, room_id: int) -> None:
    """Выходит из комнаты, сохраняя ограничения и отметку прочтения.

    Args:
        user: Участник.
        room_id: Комната.

    Raises:
        ChatAccessError: Аккаунт неактивен.
    """
    require_active_user(user)
    with transaction.atomic():
        room = _room(room_id, lock=True)
        if room.administrator_id == user.pk:
            raise ChatAccessError(
                "Сначала передайте управление другому участнику. Отзыв согласий доступен без передачи."
            )
        changed = ChatMembership.objects.filter(
            room_id=room_id, user=user, is_active=True
        ).update(is_active=False)
        if changed:
            transaction.on_commit(lambda: publish_room_change(room_id))


@transaction.atomic
def send_message(
    *,
    user: User,
    room_id: int,
    text: str,
    client_id: UUID,
    reply_to_id: int | None = None,
) -> ChatMessage:
    """Сохраняет сообщение один раз и публикует изменение после commit.

    Args:
        user: Автор.
        room_id: Комната.
        text: Текст.
        client_id: Стабильный UUID повторного запроса.
        reply_to_id: Сообщение той же комнаты для ответа.

    Returns:
        Созданное или ранее сохранённое сообщение.

    Raises:
        ChatAccessError: Нет права писать.
        ChatNotFoundError: Комната недоступна.
        ChatValidationError: Неверный текст, ключ или ответ.
        ChatRateLimitError: Слишком частая отправка.
    """
    require_active_user(user)
    _room(room_id, lock=True)
    require_member(user=user, room_id=room_id, writing=True)
    # NO KEY UPDATE сохраняет сериализацию автора, не блокируя FK уведомлений.
    User.objects.select_for_update(no_key=True).get(pk=user.pk)
    existing = ChatMessage.objects.filter(author=user, client_id=client_id).first()
    if existing:
        if existing.room_id != room_id:
            raise ChatValidationError("Ключ отправки уже использован в другом чате.")
        return existing
    cleaned = normalize_message(text)
    reply = None
    if reply_to_id is not None:
        reply = ChatMessage.objects.filter(
            room_id=room_id, pk=reply_to_id, deleted_at__isnull=True
        ).first()
        if reply is None:
            raise ChatValidationError("Нельзя ответить на это сообщение.")
    recent = timezone.now() - timedelta(seconds=MESSAGE_INTERVAL_SECONDS)
    if ChatMessage.objects.filter(author=user, created_at__gt=recent).exists():
        raise ChatRateLimitError("Подождите пару секунд перед следующим сообщением.")
    message = ChatMessage.objects.create(
        room_id=room_id, author=user, text=cleaned, client_id=client_id, reply_to=reply
    )
    _event(message)
    record_message_change(
        message=message, actor=user, action="created", text=message.text
    )
    if (
        reply
        and reply.author_id is not None
        and reply.author_id != user.pk
        and ChatMembership.objects.filter(
            room_id=room_id,
            user_id=reply.author_id,
            is_active=True,
            notifications_enabled=True,
        ).exists()
    ):
        Notification.objects.create(
            user_id=reply.author_id,
            message=f"Вам ответили в городском чате «{message.room.display_name}»"[
                :255
            ],
            url=reverse("chat:room", args=[room_id]),
        )
    logger.info(
        "Сообщение городского чата создано",
        room_id=room_id,
        message_id=message.pk,
        user_id=user.pk,
    )
    return message


def history(
    *, user: User, room_id: int, before_id: int | None = None
) -> dict[str, Any]:
    """Загружает страницу истории и курсор последующих изменений.

    Args:
        user: Участник.
        room_id: Комната.
        before_id: Исключительная верхняя граница истории.

    Returns:
        Сообщения в порядке отправки, курсор и признак предыдущей страницы.

    Raises:
        ChatAccessError: Нет доступа к истории.
    """
    require_member(user=user, room_id=room_id)
    cursor = (
        ChatEvent.objects.filter(room_id=room_id)
        .order_by("-id")
        .values_list("id", flat=True)
        .first()
        or 0
    )
    query = ChatMessage.objects.filter(room_id=room_id).select_related(
        "author", "author__player", "reply_to", "reply_to__author"
    )
    if before_id:
        query = query.filter(pk__lt=before_id)
    rows = list(query.order_by("-id")[: PAGE_SIZE + 1])
    moderator_ids = _moderator_ids(room_id)
    disclosures = _message_disclosures(room_id, rows)
    return {
        "messages": [
            serialize_message(m, moderator_ids=moderator_ids, disclosures=disclosures)
            for m in reversed(rows[:PAGE_SIZE])
        ],
        "has_more": len(rows) > PAGE_SIZE,
        "cursor": cursor,
    }


def sync_events(*, user: User, room_id: int, after_id: int) -> dict[str, Any]:
    """Восстанавливает все изменения, включая правки старых сообщений.

    Args:
        user: Участник.
        room_id: Комната.
        after_id: Последний полученный курсор журнала.

    Returns:
        Упорядоченные изменения и курсор следующей страницы.

    Raises:
        ChatAccessError: Нет доступа.
    """
    member = require_member(user=user, room_id=room_id)
    rows = list(
        ChatEvent.objects.filter(room_id=room_id, pk__gt=after_id)
        .select_related(
            "message__author",
            "message__author__player",
            "message__reply_to",
            "message__reply_to__author",
        )
        .order_by("id")[: MAX_PAGE_SIZE + 1]
    )
    from .invitations import can_invite

    moderator_ids = _moderator_ids(room_id)
    disclosures = _message_disclosures(room_id, [event.message for event in rows])
    return {
        "events": [
            {
                "id": e.pk,
                "message": serialize_message(
                    e.message, moderator_ids=moderator_ids, disclosures=disclosures
                ),
            }
            for e in rows[:MAX_PAGE_SIZE]
        ],
        "cursor": (
            rows[MAX_PAGE_SIZE - 1].pk
            if len(rows) > MAX_PAGE_SIZE
            else (rows[-1].pk if rows else after_id)
        ),
        "has_more": len(rows) > MAX_PAGE_SIZE,
        "can_moderate": _moderator(user, member),
        "can_manage": can_manage(user=user, room=member.room),
        "can_invite": can_invite(user=user, room_id=room_id),
        "moderator_ids": sorted(moderator_ids),
        "administrator_id": (
            member.room.administrator_id
            if member.room.administrator_id in moderator_ids
            else None
        ),
        "banned_until": (
            member.banned_until.isoformat() if member.banned_until else None
        ),
        "rules": member.room.rules,
    }


@transaction.atomic
def edit_message(*, user: User, room_id: int, message_id: int, text: str) -> None:
    """Редактирует своё сообщение в разрешённое окно.

    Args:
        user: Автор.
        room_id: Комната.
        message_id: Сообщение.
        text: Новый текст.

    Raises:
        ChatError: Сообщение недоступно, срок истёк или текст неверен.
    """
    _room(room_id, lock=True)
    require_member(user=user, room_id=room_id, writing=True)
    message = _message(room_id, message_id)
    if (
        message.author_id != user.pk
        or message.deleted_at
        or timezone.now() - message.created_at > timedelta(seconds=EDIT_WINDOW_SECONDS)
    ):
        raise ChatAccessError(
            "Можно редактировать только своё сообщение в течение 15 минут."
        )
    cleaned = normalize_message(text)
    ensure_message_snapshot(message)
    message.text = cleaned
    message.edited_at = timezone.now()
    message.save(update_fields=["text", "edited_at"])
    _event(message)
    record_message_change(
        message=message, actor=user, action="edited", text=message.text
    )


@transaction.atomic
def delete_message(*, user: User, room_id: int, message_id: int) -> None:
    """Удаляет текст своего сообщения либо сообщения, нарушающего правила.

    Args:
        user: Автор или модератор.
        room_id: Комната.
        message_id: Сообщение.

    Raises:
        ChatError: Нет прав или сообщение не найдено.
    """
    _room(room_id, lock=True)
    member = require_member(user=user, room_id=room_id)
    message = _message(room_id, message_id)
    if message.author_id != user.pk and not _moderator(user, member):
        raise ChatAccessError("Удалить сообщение может автор или модератор.")
    if not message.deleted_at:
        ensure_message_snapshot(message)
        previous_text = message.text
        message.text = ""
        message.deleted_at = timezone.now()
        message.save(update_fields=["text", "deleted_at"])
        _event(message)
        record_message_change(
            message=message, actor=user, action="deleted", text=previous_text
        )
        logger.info(
            "Сообщение чата удалено",
            room_id=room_id,
            message_id=message_id,
            user_id=user.pk,
        )


def mark_read(*, user: User, room_id: int, message_id: int) -> None:
    """Монотонно продвигает прочтение до существующего сообщения комнаты.

    Args:
        user: Читатель.
        room_id: Комната.
        message_id: Последнее показанное сообщение.

    Raises:
        ChatError: Нет доступа либо граница не принадлежит комнате.
    """
    require_member(user=user, room_id=room_id)
    if not ChatMessage.objects.filter(room_id=room_id, pk=message_id).exists():
        raise ChatValidationError("Неверная отметка прочтения.")
    ChatMembership.objects.filter(
        user=user, room_id=room_id, last_read_message_id__lt=message_id
    ).update(last_read_message_id=message_id)


def set_notifications(*, user: User, room_id: int, enabled: bool) -> None:
    """Настраивает уведомления об ответах в одной комнате.

    Args:
        user: Участник.
        room_id: Комната.
        enabled: Новое значение настройки.

    Raises:
        ChatAccessError: Нет участия.
    """
    require_member(user=user, room_id=room_id)
    ChatMembership.objects.filter(user=user, room_id=room_id).update(
        notifications_enabled=enabled
    )


def report_message(*, user: User, room_id: int, message_id: int, reason: str) -> None:
    """Отправляет не более одной жалобы участника на сообщение.

    Args:
        user: Заявитель.
        room_id: Комната.
        message_id: Обжалуемое сообщение.
        reason: Причина.

    Raises:
        ChatError: Нет доступа, сообщения или корректной причины.
    """
    require_member(user=user, room_id=room_id, writing=True)
    message = _message(room_id, message_id)
    reason = normalize_message(reason)
    if not reason or len(reason) > MAX_REPORT_LENGTH or "\x00" in reason:
        raise ChatValidationError("Укажите причину жалобы до 500 символов.")
    ChatReport.objects.get_or_create(
        message=message, reporter=user, defaults={"reason": reason}
    )


@transaction.atomic
def moderate_member(*, user: User, room_id: int, target_id: int, banned: bool) -> None:
    """Запрещает писать на сутки либо снимает запрет.

    Args:
        user: Модератор.
        room_id: Комната.
        target_id: Нарушитель.
        banned: Установить запрет вместо снятия.

    Raises:
        ChatError: Недостаточно прав, участник не найден или защищён.
    """
    _room(room_id, lock=True)
    member = require_member(user=user, room_id=room_id)
    if not _moderator(user, member):
        raise ChatAccessError("Требуются права модератора.")
    target = (
        ChatMembership.objects.select_related("user")
        .filter(room_id=room_id, user_id=target_id)
        .first()
    )
    if target is None:
        raise ChatNotFoundError("Участник не найден.")
    if (
        target.user.is_staff
        or target.is_moderator
        or target_id == user.pk
        or target_id == member.room.administrator_id
    ):
        raise ChatAccessError("Нельзя ограничить этого участника.")
    target.banned_until = (
        timezone.now() + timedelta(days=BAN_DURATION_DAYS) if banned else None
    )
    target.save(update_fields=["banned_until"])
    transaction.on_commit(lambda: publish_room_change(room_id))
    logger.info(
        "Изменено ограничение участника чата",
        room_id=room_id,
        target_id=target_id,
        moderator_id=user.pk,
        banned=banned,
    )


def my_memberships(
    *, user: User
) -> QuerySet[WithAnnotations[ChatMembership, _MembershipCounts]]:
    """Возвращает комнаты с численностью участников и количеством непрочитанных.

    Args:
        user: Участник платформы.

    Returns:
        Участие, город, число активных читателей и непрочитанных чужих сообщений.

    Raises:
        ChatAccessError: Неактивный аккаунт.
    """
    require_active_user(user)
    # Подзапрос не перемножает участников на сообщения при подсчёте непрочитанного.
    member_counts = (
        ChatMembership.objects.filter(
            room_id=OuterRef("room_id"), is_active=True, user__is_active=True
        )
        .order_by()
        .values("room_id")
        .annotate(total=Count("pk"))
        .values("total")
    )
    return (
        ChatMembership.objects.filter(user=user, is_active=True, room__is_active=True)
        .select_related("room__city")
        .annotate(
            member_count=Coalesce(
                Subquery(member_counts, output_field=IntegerField()), 0
            ),
            unread=Count(
                "room__messages",
                filter=Q(
                    room__messages__id__gt=F("last_read_message_id"),
                    room__messages__deleted_at__isnull=True,
                )
                & ~Q(room__messages__author=user),
            ),
        )
        .order_by("room__city__name")
    )
