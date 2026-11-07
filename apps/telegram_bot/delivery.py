"""Долговечная очередь Telegram с арендой отправителя и честным исходом доставки."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Final

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Count, Max, QuerySet
from django.utils import timezone
from django.utils.html import strip_tags
from loguru import logger

from apps.core.models import UserTelegramLink
from apps.notifications.domain.rules import BODY_LIMIT, TITLE_LIMIT
from apps.notifications.models import Delivery
from apps.users.models import User

from . import services
from .binding import lock_user_bot_link
from .models import TelegramBroadcast, TelegramDeliveryState, TournamentAnnouncement
from .transport import TelegramAPIError

LEASE: Final = timedelta(minutes=5)
LIFETIME: Final = timedelta(days=1)
MAX_ATTEMPTS: Final = 5
RETRY_BASE_SECONDS: Final = 60
BATCH_SIZE: Final = 100
BOT: Final = "user"


def _recipient_links() -> QuerySet[UserTelegramLink]:
    """Выбрать только активные явные привязки к личному чату бота."""
    return UserTelegramLink.objects.filter(user__is_active=True, user_bot_chat_id__gt=0)


def enqueue_message(
    user: User | None,
    text: str,
    reply_markup: dict[str, Any] | None = None,
    *,
    event_key: str = "platform.user_message",
    photo_name: str = "",
    expires_at: datetime | None = None,
    key: uuid.UUID | None = None,
    broadcast: TelegramBroadcast | None = None,
    announcement: TournamentAnnouncement | None = None,
) -> Delivery | None:
    """Сохранить сообщение, не обращаясь к сети; наличие записи не означает отправку.

    Повтор с тем же ключом возвращает исходное задание. chat_id фиксируется
    при постановке и проверяется снова перед отправкой: старый контекст не
    должен попасть в новый чат после перепривязки.
    """
    if user is None or user.pk is None or not text.strip():
        return None
    link = _recipient_links().filter(user_id=user.pk).first()
    if link is None:
        return None
    payload: dict[str, Any] = {
        "chat_id": link.user_bot_chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if photo_name:
        payload["photo_name"] = photo_name
    now = timezone.now()
    values = {
        "user_id": user.pk,
        "channel": "telegram",
        "event_key": event_key,
        "title": (strip_tags(text).splitlines() or ["Telegram"])[0][:TITLE_LIMIT],
        "body": strip_tags(text)[:BODY_LIMIT],
        "url": "",
        "telegram_payload": payload,
        "telegram_broadcast": broadcast,
        "tournament_announcement": announcement,
        "available_at": now,
        "expires_at": expires_at or now + LIFETIME,
    }
    if key is not None:
        return Delivery.objects.get_or_create(key=key, defaults=values)[0]
    if broadcast is not None:
        return Delivery.objects.get_or_create(
            telegram_broadcast=broadcast,
            user_id=user.pk,
            channel="telegram",
            defaults={
                name: value
                for name, value in values.items()
                if name not in {"telegram_broadcast", "user_id", "channel"}
            },
        )[0]
    if announcement is not None:
        return Delivery.objects.get_or_create(
            tournament_announcement=announcement,
            user_id=user.pk,
            channel="telegram",
            defaults={
                name: value
                for name, value in values.items()
                if name not in {"tournament_announcement", "user_id", "channel"}
            },
        )[0]
    return Delivery.objects.create(**values)


@transaction.atomic
def request_broadcast(broadcast_pk: int) -> bool:
    """Поставить подготовку рассылки в очередь в той же транзакции, что и сохранение."""
    broadcast = TelegramBroadcast.objects.select_for_update().get(pk=broadcast_pk)
    if broadcast.status != TelegramBroadcast.Status.DRAFT or broadcast.sent_at:
        return False
    if not broadcast.text.strip():
        return False
    broadcast.upper_user_id = (
        _recipient_links().aggregate(value=Max("user_id"))["value"] or 0
    )
    broadcast.status = TelegramBroadcast.Status.QUEUED
    broadcast.save(update_fields=["upper_user_id", "status"])
    return True


@transaction.atomic
def enqueue_broadcast(broadcast_pk: int, *, batch_size: int = BATCH_SIZE) -> int:
    """Подготовить ограниченный пакет; курсор и задания сохраняются атомарно."""
    if batch_size <= 0:
        return 0
    broadcast = TelegramBroadcast.objects.select_for_update().get(pk=broadcast_pk)
    if broadcast.status == TelegramBroadcast.Status.DRAFT:
        request_broadcast(broadcast_pk)
        broadcast.refresh_from_db()
    if broadcast.status != TelegramBroadcast.Status.QUEUED or broadcast.prepared_at:
        return 0
    links = list(
        _recipient_links()
        .filter(
            user_id__gt=broadcast.cursor_user_id, user_id__lte=broadcast.upper_user_id
        )
        .select_related("user")
        .order_by("user_id")[:batch_size]
    )
    for link in links:
        enqueue_message(
            link.user,
            broadcast.text.strip(),
            broadcast=broadcast,
            event_key="platform.manual_broadcast",
            photo_name=broadcast.image.name if broadcast.image else "",
            expires_at=broadcast.created_at + LIFETIME,
        )
        broadcast.cursor_user_id = link.user_id
    if (
        not _recipient_links()
        .filter(
            user_id__gt=broadcast.cursor_user_id, user_id__lte=broadcast.upper_user_id
        )
        .exists()
    ):
        broadcast.prepared_at = timezone.now()
    broadcast.save(update_fields=["cursor_user_id", "prepared_at"])
    refresh_broadcast_status(broadcast.pk)
    return len(links)


@transaction.atomic
def refresh_broadcast_status(broadcast_pk: int) -> None:
    """Завершить рассылку только после подготовки и финального исхода каждого задания."""
    broadcast = TelegramBroadcast.objects.select_for_update().get(pk=broadcast_pk)
    if not broadcast.prepared_at or broadcast.status != TelegramBroadcast.Status.QUEUED:
        return
    counts = dict(
        broadcast.deliveries.values("status")
        .annotate(total=Count("pk"))
        .values_list("status", "total")
    )
    if counts.get("queued", 0) or counts.get("processing", 0):
        return
    total = sum(counts.values())
    accepted = counts.get("accepted", 0)
    if total == 0:
        broadcast.status = TelegramBroadcast.Status.EMPTY
    elif accepted == total:
        broadcast.status = TelegramBroadcast.Status.SENT
        broadcast.sent_at = timezone.now()
    elif accepted:
        broadcast.status = TelegramBroadcast.Status.PARTIAL
    else:
        broadcast.status = TelegramBroadcast.Status.FAILED
    broadcast.finished_at = timezone.now()
    broadcast.save(update_fields=["status", "sent_at", "finished_at"])


def _finish(item: Delivery, status: str, code: str = "", provider_id: str = "") -> None:
    """Закрыть только текущую попытку, не перезаписывая результат другого worker."""
    Delivery.objects.filter(
        pk=item.pk, status="processing", attempts=item.attempts
    ).update(
        status=status,
        error_code=code,
        provider_id=provider_id,
        finished_at=timezone.now(),
    )


@transaction.atomic
def _claim() -> Delivery | None:
    """Атомарно арендовать отправителя и одно задание, учитывая общую паузу 429."""
    now = timezone.now()
    TelegramDeliveryState.objects.get_or_create(
        bot=BOT, defaults={"cooldown_until": now}
    )
    state = (
        TelegramDeliveryState.objects.select_for_update(skip_locked=True)
        .filter(bot=BOT)
        .first()
    )
    if state is None:
        return None
    now = timezone.now()
    if state.cooldown_until > now or (state.lease_until and state.lease_until > now):
        return None
    item = (
        Delivery.objects.select_for_update(skip_locked=True)
        .filter(channel="telegram", status="queued", available_at__lte=now)
        .order_by("available_at", "pk")
        .first()
    )
    if item is None:
        return None
    item.status = "processing"
    item.attempts += 1
    item.available_at = now + LEASE
    item.save(update_fields=["status", "attempts", "available_at"])
    state.lease_key = item.key
    state.lease_until = item.available_at
    state.save(update_fields=["lease_key", "lease_until"])
    return item


def _retry(item: Delivery, error: TelegramAPIError) -> None:
    """Повторить только явный временный отказ или сбой соединения до отправки."""
    now = timezone.now()
    delay = error.retry_after or RETRY_BASE_SECONDS * 2 ** (item.attempts - 1)
    if error.retry_after is not None:
        TelegramDeliveryState.objects.filter(
            bot=BOT, cooldown_until__lt=now + timedelta(seconds=delay)
        ).update(cooldown_until=now + timedelta(seconds=delay))
    if item.attempts >= MAX_ATTEMPTS:
        _finish(item, "failed", error.code)
        return
    Delivery.objects.filter(
        pk=item.pk, status="processing", attempts=item.attempts
    ).update(
        status="queued",
        error_code=error.code,
        available_at=now + timedelta(seconds=delay),
    )


def _close_expired_attempt(item: Delivery) -> bool:
    """Повторно проверить TTL и аренду после любого ожидания до обращения к Telegram."""
    now = timezone.now()
    if item.expires_at <= now:
        _finish(item, "skipped", "telegram_expired")
        return True
    if item.available_at <= now:
        _finish(item, "uncertain", "telegram_worker_interrupted")
        return True
    return False


@transaction.atomic
def _process(item: Delivery) -> None:
    """Держать свежие задание и разрешение адресата заблокированными до ответа сети."""
    state = TelegramDeliveryState.objects.select_for_update().filter(bot=BOT).first()
    fresh = (
        Delivery.objects.select_for_update(no_key=True)
        .filter(
            pk=item.pk,
            key=item.key,
            channel="telegram",
            status="processing",
            attempts=item.attempts,
        )
        .first()
    )
    if fresh is None:
        return
    item = fresh
    if (
        state is None
        or state.lease_key != item.key
        or state.lease_until != item.available_at
    ):
        _finish(item, "uncertain", "telegram_sender_lease_lost")
        return
    payload = item.telegram_payload
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        _finish(item, "failed", "telegram_payload_invalid")
        return
    chat_id = payload.get("chat_id")
    if not isinstance(chat_id, int) or isinstance(chat_id, bool) or chat_id <= 0:
        _finish(item, "failed", "telegram_recipient_invalid")
        return
    if _close_expired_attempt(item):
        return
    if lock_user_bot_link(chat_id, user_id=item.user_id) is None:
        _finish(item, "skipped", "telegram_binding_changed")
        return
    if _close_expired_attempt(item):
        return
    announcement = item.tournament_announcement
    if announcement is not None:
        from .announcements import telegram_recipient_allowed

        if not telegram_recipient_allowed(announcement.tournament, item.user_id):
            _finish(item, "skipped", "telegram_audience_changed")
            return
    photo_name = payload.get("photo_name")
    keyboard = payload.get("reply_markup")
    if (keyboard is not None and not isinstance(keyboard, dict)) or (
        photo_name is not None and not isinstance(photo_name, str)
    ):
        _finish(item, "failed", "telegram_payload_invalid")
        return
    if photo_name:
        try:
            with default_storage.open(photo_name, "rb") as photo:
                content = photo.read()
                if _close_expired_attempt(item):
                    return
                message_id = services.send_photo_or_raise(
                    chat_id, content, caption=payload["text"], reply_markup=keyboard
                )
        except OSError:
            raise TelegramAPIError("telegram_photo_unavailable") from None
    else:
        if _close_expired_attempt(item):
            return
        message_id = services.send_message_or_raise(
            chat_id, payload["text"], reply_markup=keyboard
        )
    _finish(item, "accepted", provider_id=str(message_id))


@transaction.atomic
def _recover_deliveries(now: datetime, *, limit: int) -> None:
    """Закрыть брошенные задания, не ожидая строки активной сетевой отправки."""
    for status, time_field, final_status, code in (
        ("processing", "available_at", "uncertain", "telegram_worker_interrupted"),
        ("queued", "expires_at", "skipped", "telegram_expired"),
    ):
        ids = list(
            Delivery.objects.select_for_update(skip_locked=True)
            .filter(channel="telegram", status=status, **{f"{time_field}__lte": now})
            .order_by("pk")
            .values_list("pk", flat=True)[: max(0, limit)]
        )
        Delivery.objects.filter(pk__in=ids, status=status).update(
            status=final_status, error_code=code, finished_at=now
        )


def process_telegram_deliveries(*, limit: int = BATCH_SIZE) -> int:
    """Обработать конечный пакет независимо от флага push/email-центра.

    После потери ответа или истечения аренды отправка закрывается как uncertain:
    Telegram не предоставляет идемпотентную повторную отправку сообщения.
    """
    now = timezone.now()
    _recover_deliveries(now, limit=limit)
    completed = (
        TelegramBroadcast.objects.filter(status="queued", prepared_at__isnull=False)
        .exclude(deliveries__status__in=("queued", "processing"))
        .order_by("pk")
    )
    for broadcast_pk in completed.values_list("pk", flat=True)[: max(0, limit)]:
        refresh_broadcast_status(broadcast_pk)
    prepared = 0
    pending = TelegramBroadcast.objects.filter(
        status="queued", prepared_at__isnull=True
    ).order_by("pk")
    for broadcast_pk in pending.values_list("pk", flat=True)[: max(0, limit)]:
        try:
            prepared += enqueue_broadcast(broadcast_pk, batch_size=limit - prepared)
        except Exception as exc:
            logger.error(
                "Ошибка подготовки Telegram-рассылки: id={}, error={}",
                broadcast_pk,
                type(exc).__name__,
            )
        if prepared >= limit:
            break
    if not services.is_configured():
        return 0
    handled = 0
    for _ in range(max(0, limit)):
        item = _claim()
        if item is None:
            break
        handled += 1
        try:
            _process(item)
        except TelegramAPIError as exc:
            if exc.uncertain:
                _finish(item, "uncertain", exc.code)
            elif exc.retryable:
                _retry(item, exc)
            elif exc.code in {"telegram_disabled", "telegram_not_configured"}:
                Delivery.objects.filter(
                    pk=item.pk, status="processing", attempts=item.attempts
                ).update(
                    status="queued",
                    attempts=item.attempts - 1,
                    available_at=timezone.now(),
                )
            else:
                _finish(item, "failed", exc.code)
            logger.warning("Telegram-доставка: id={}, code={}", item.pk, exc.code)
        except Exception as exc:
            _finish(item, "uncertain", "telegram_worker_internal_error")
            logger.error(
                "Сбой Telegram-worker: id={}, error={}", item.pk, type(exc).__name__
            )
        finally:
            TelegramDeliveryState.objects.filter(
                bot=BOT, lease_key=item.key, lease_until=item.available_at
            ).update(lease_until=None, lease_key=None)
        if item.telegram_broadcast_id is not None:
            refresh_broadcast_status(item.telegram_broadcast_id)
    return handled
