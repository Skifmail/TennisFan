"""Оркестрация событий, согласий и пакетной подготовки рассылок."""

import uuid
from datetime import timedelta
from typing import Final

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from loguru import logger

from apps.users.models import Notification, User

from .domain.catalog import EVENTS, INBOX_ONLY_EVENTS
from .domain.rules import (
    BODY_LIMIT,
    TITLE_LIMIT,
    NotificationValidationError,
    validate_content,
    validate_destination,
)
from .models import Broadcast, Delivery, EventPolicy, NotificationPreference
from .presence import is_in_chat

DELIVERY_LIFETIME: Final = timedelta(days=1)
BATCH_SIZE: Final = 100


def center_enabled() -> bool:
    """Проверяет флаг поэтапного подключения, не обращаясь к БД.

    Returns:
        Включён ли центр уведомлений после миграции и настройки секретов.
    """
    return bool(settings.NOTIFICATION_CENTER_ENABLED)


def event_policy(key: str) -> EventPolicy:
    """Читает явную политику без автоматического создания неизвестных событий.

    Args:
        key: Стабильный код события.

    Returns:
        Сохранённые администратором разрешения.

    Raises:
        NotificationValidationError: Событие не зарегистрировано или миграция не применена.
    """
    if key not in EVENTS:
        raise NotificationValidationError("Неизвестное событие уведомления.")
    try:
        return EventPolicy.objects.get(key=key)
    except EventPolicy.DoesNotExist as exc:
        raise NotificationValidationError(
            "Не применена миграция реестра уведомлений."
        ) from exc


def preferences(user_id: int) -> tuple[bool, bool]:
    """Читает два согласия без создания записи во время GET.

    Args:
        user_id: Владелец настроек.

    Returns:
        Push по явному согласию и email с сохранением прежнего поведения.
    """
    row = NotificationPreference.objects.filter(user_id=user_id).first()
    return (row.push_enabled, row.email_enabled) if row else (False, True)


def _safe_url(url: str) -> str:
    try:
        return validate_destination(url, settings.ONESIGNAL_SITE_ORIGIN)
    except NotificationValidationError:
        return validate_destination("", settings.ONESIGNAL_SITE_ORIGIN)


def _queue_push(
    note: Notification,
    title: str,
    recipient_id: int,
    chat_room_id: int | None = None,
) -> None:
    """Ставит одно push-задание сразу после события.

    Если получатель уже открыл комнату чата, push не создаётся: уведомление
    остаётся в ЛК, а worker дополнительно перепроверит присутствие перед отправкой.

    Args:
        note: Запись ЛК, к которой относится push.
        title: Заголовок события.
        recipient_id: Получатель без дополнительного запроса пользователя.
        chat_room_id: Комната чата для подавления push, если она открыта.
    """
    push, _ = preferences(recipient_id)
    if not (push and event_policy(note.event_key).push_enabled):
        return
    if chat_room_id is not None and is_in_chat(recipient_id, chat_room_id):
        return
    now = timezone.now()
    Delivery.objects.create(
        event_key=note.event_key,
        user_id=recipient_id,
        channel="push",
        notification=note,
        title=title[:TITLE_LIMIT],
        body=note.message[:BODY_LIMIT],
        url=_safe_url(note.url),
        chat_room_id=chat_room_id,
        available_at=now,
        expires_at=now + DELIVERY_LIFETIME,
    )


@transaction.atomic
def create_notification(
    *,
    event_key: str,
    message: str,
    user: User | None = None,
    user_id: int | None = None,
    url: str = "",
    chat_room_id: int | None = None,
) -> Notification:
    """Сохраняет запись ЛК и атомарно создаёт единственное push-задание.

    Args:
        event_key: Явный ключ события, не классификация текста.
        message: Прежний текст уведомления ЛК.
        user: Получатель либо ``None`` при передаче user_id.
        user_id: ID получателя вместо объекта.
        url: Ссылка на соответствующий объект платформы.
        chat_room_id: Комната чата, открытость которой подавляет push.

    Returns:
        Запись с PK даже при отключённой видимости ЛК для сохранения связей.

    Raises:
        NotificationValidationError: Не указан получатель или неизвестно событие.
    """
    recipient_id = user.pk if user is not None else user_id
    if recipient_id is None:
        raise NotificationValidationError("Не указан получатель уведомления.")
    visible = event_policy(event_key).inbox_enabled if center_enabled() else True
    note = Notification.objects.create(
        user_id=recipient_id,
        message=message,
        url=url,
        event_key=event_key,
        is_visible=visible,
    )
    if center_enabled() and EVENTS[event_key].source == "inbox":
        _queue_push(note, EVENTS[event_key].title, recipient_id, chat_room_id)
        if (
            event_key in INBOX_ONLY_EVENTS
            and event_policy(event_key).email_enabled
            and preferences(recipient_id)[1]
        ):
            now = timezone.now()
            Delivery.objects.create(
                event_key=event_key,
                user_id=recipient_id,
                channel="email",
                notification=note,
                title=EVENTS[event_key].title[:TITLE_LIMIT],
                body=message[:BODY_LIMIT],
                url=_safe_url(url),
                available_at=now,
                expires_at=now + DELIVERY_LIFETIME,
            )
    return note


@transaction.atomic
def capture_email_event(event_key: str, user: User) -> None:
    """Создаёт нейтральное уведомление для события с почтовым источником.

    Секреты и содержимое письма намеренно не передаются в ЛК и на экран устройства.

    Args:
        event_key: Событие почтового адаптера.
        user: Получатель письма в платформе.
    """
    definition = EVENTS[event_key]
    if definition.source != "email":
        return
    note = create_notification(
        event_key=event_key,
        user=user,
        message=f"{definition.title}. Подробности доступны в личном кабинете.",
        url="/users/notifications/",
    )
    _queue_push(note, definition.title, user.pk)


@transaction.atomic
def queue_broadcast(broadcast_id: int, *, actor: User) -> Broadcast:
    """Подтверждает неизменяемый снимок рассылки однократно.

    Args:
        broadcast_id: Черновик, который администратор предварительно просмотрел.
        actor: Автор подтверждения, проверенный транспортным слоем.

    Returns:
        Подтверждённая рассылка; повторный POST не создаёт новый запуск.

    Raises:
        NotificationValidationError: Пустое, длинное содержимое, ссылка или нет каналов.
    """
    campaign = Broadcast.objects.select_for_update().get(pk=broadcast_id)
    if campaign.status != "draft":
        return campaign
    if not center_enabled():
        raise NotificationValidationError("Центр уведомлений пока не активирован.")
    campaign.title, campaign.body = validate_content(campaign.title, campaign.body)
    campaign.url = validate_destination(campaign.url, settings.ONESIGNAL_SITE_ORIGIN)
    if not any((campaign.send_push, campaign.send_email, campaign.send_inbox)):
        raise NotificationValidationError("Выберите хотя бы один канал.")
    if campaign.submission_id is None:
        campaign.submission_id = uuid.uuid4()
    campaign.upper_user_id = User.objects.aggregate(value=Max("pk"))["value"] or 0
    campaign.status = "queued"
    campaign.confirmed_by = actor
    campaign.confirmed_at = timezone.now()
    campaign.save()
    logger.info(
        "Рассылка подтверждена",
        broadcast_id=campaign.pk,
        actor_id=actor.pk,
        test=campaign.is_test,
    )
    return campaign


@transaction.atomic
def expand_broadcast(broadcast_id: int, *, batch_size: int = BATCH_SIZE) -> int:
    """Формирует пакет адресатов под блокировкой курсора, без сетевых запросов.

    Args:
        broadcast_id: Подтверждённая рассылка.
        batch_size: Максимальное число пользователей за транзакцию.

    Returns:
        Число просмотренных пользователей; новые регистрации в снимок не входят.
    """
    campaign = Broadcast.objects.select_for_update().get(pk=broadcast_id)
    if campaign.status != "queued" or not center_enabled():
        return 0
    users = User.objects.filter(
        is_active=True, pk__gt=campaign.cursor_user_id, pk__lte=campaign.upper_user_id
    )
    if campaign.is_test:
        users = users.filter(pk=campaign.target_user_id)
    rows = list(users.order_by("pk")[:batch_size])
    policy = event_policy("manual.broadcast")
    now = timezone.now()
    for user in rows:
        push, email = preferences(user.pk)
        note = None
        if campaign.send_inbox and policy.inbox_enabled:
            note = Notification.objects.create(
                user=user,
                message=f"{campaign.title}: {campaign.body}",
                url=campaign.url,
                event_key="manual.broadcast",
            )
        for channel, allowed in (
            ("push", campaign.send_push and policy.push_enabled and push),
            ("email", campaign.send_email and policy.email_enabled and email),
        ):
            if allowed:
                Delivery.objects.get_or_create(
                    broadcast=campaign,
                    user=user,
                    channel=channel,
                    defaults={
                        "event_key": "manual.broadcast",
                        "notification": note,
                        "title": campaign.title,
                        "body": campaign.body,
                        "url": campaign.url,
                        "available_at": now,
                        "expires_at": now + DELIVERY_LIFETIME,
                    },
                )
        campaign.processed_users += 1
        if note is not None:
            campaign.inbox_created += 1
        campaign.cursor_user_id = user.pk
    if len(rows) < batch_size:
        campaign.status = "expanded"
    campaign.save(
        update_fields=["cursor_user_id", "status", "processed_users", "inbox_created"]
    )
    return len(rows)
