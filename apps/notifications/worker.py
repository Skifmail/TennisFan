"""Пакетная доставка с арендой задания, повторной проверкой согласий и TTL."""

import smtplib
from datetime import datetime, timedelta
from typing import Final

from django.core.mail import get_connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from loguru import logger

from .device_binding import revoke_devices
from .domain.catalog import EVENTS
from .domain.rules import NotificationValidationError, channels_for
from .infrastructure.email import EventEmail
from .infrastructure.push import PushRejectedError, PushTransportError, send_push
from .models import Broadcast, Delivery, PushDevice
from .presence import is_in_chat
from .services import center_enabled, event_policy, expand_broadcast, preferences

LEASE: Final = timedelta(minutes=5)
MAX_ATTEMPTS: Final = 5
RETRY_BASE_SECONDS: Final = 60
SMTP_TIMEOUT: Final = 10


def _finish(item: Delivery, status: str, code: str = "", provider_id: str = "") -> None:
    Delivery.objects.filter(
        pk=item.pk, status="processing", attempts=item.attempts
    ).update(
        status=status,
        error_code=code,
        provider_id=provider_id,
        finished_at=timezone.now(),
    )


def _retry(item: Delivery, code: str) -> None:
    """Возвращает задание в очередь с экспоненциальной паузой.

    Args:
        item: Задание, уже переведённое в ``processing``.
        code: Безопасный код причины повтора.
    """
    if item.attempts >= MAX_ATTEMPTS:
        _finish(item, "failed", code)
        return
    Delivery.objects.filter(
        pk=item.pk, status="processing", attempts=item.attempts
    ).update(
        status="queued",
        error_code=code,
        available_at=timezone.now()
        + timedelta(seconds=RETRY_BASE_SECONDS * 2 ** (item.attempts - 1)),
    )


def _expire_interrupted_email(now: datetime) -> int:
    """Помечает письма, чей worker не завершил аренду, как неопределённые.

    SMTP не поддерживает идемпотентность, поэтому повторять письмо вслепую нельзя.

    Args:
        now: Текущий момент.

    Returns:
        Число закрытых заданий.
    """
    count = Delivery.objects.filter(
        status="processing", channel="email", available_at__lte=now
    ).update(
        status="uncertain",
        error_code="smtp_worker_interrupted",
        finished_at=now,
    )
    if count:
        logger.warning("Закрыты прерванные email-доставки", count=count)
    return count


@transaction.atomic
def _claim() -> Delivery | None:
    """Захватывает одно задание, не останавливаясь на прерванном письме.

    Returns:
        Задание в состоянии ``processing`` либо ``None`` при пустой очереди.
    """
    now = timezone.now()
    while True:
        item = (
            Delivery.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status="queued") | Q(status="processing"),
                available_at__lte=now,
            )
            .order_by("available_at", "pk")
            .first()
        )
        if item is None:
            return None
        # SMTP не поддерживает идемпотентность: после падения процесса не повторяем вслепую.
        if item.status == "processing" and item.channel == "email":
            _finish(item, "uncertain", "smtp_worker_interrupted")
            logger.warning(
                "Прерванная email-доставка закрыта без повтора", delivery_id=item.pk
            )
            continue
        item.status = "processing"
        item.attempts += 1
        item.available_at = now + LEASE
        item.save(update_fields=["status", "attempts", "available_at"])
        return item


def _skip_pending_chat(item: Delivery) -> bool:
    """Проверяет, что получатель уже открыл комнату и push не нужен.

    Args:
        item: Задание push.

    Returns:
        Истина, если задание следует пометить просмотренным.
    """
    return item.chat_room_id is not None and is_in_chat(item.user_id, item.chat_room_id)


def _validation_code(item: Delivery) -> str:
    """Возвращает безопасный код отказа политики события.

    Args:
        item: Задание с неизвестным или неполным событием.

    Returns:
        Код для журнала без текста исключения.
    """
    return "unknown_event" if item.event_key not in EVENTS else "event_policy_missing"


def _process(item: Delivery) -> None:
    """Выполняет одну доставку с повторной проверкой всех согласий.

    Args:
        item: Захваченное задание в состоянии ``processing``.

    Raises:
        NotificationValidationError: Событие отсутствует в реестре или политике.
    """
    policy = event_policy(item.event_key)
    push, email = preferences(item.user_id)
    user_active = item.user.is_active
    campaign_active = item.broadcast is None or item.broadcast.status != "cancelled"
    allowed_push, allowed_email, _ = channels_for(
        (policy.push_enabled, policy.email_enabled, policy.inbox_enabled),
        push=push,
        email=email,
    )
    allowed = allowed_push if item.channel == "push" else allowed_email
    if (
        not user_active
        or not allowed
        or not campaign_active
        or item.expires_at <= timezone.now()
    ):
        _finish(item, "skipped", "consent_policy_or_expiry")
        return
    if item.channel == "push":
        if _skip_pending_chat(item):
            _finish(item, "skipped", "seen_in_chat")
            return
        subscription_ids = [
            str(value)
            for value in PushDevice.objects.filter(
                user_id=item.user_id,
                active=True,
                expires_at__gt=timezone.now(),
                subscription_id__isnull=False,
            ).values_list("subscription_id", flat=True)
        ]
        if not subscription_ids:
            _finish(item, "skipped", "no_active_device")
            return
        try:
            provider_id = send_push(
                subscription_ids=subscription_ids,
                title=item.title,
                body=item.body,
                url=item.url,
                key=str(item.key),
            )
        except PushRejectedError as exc:
            if str(exc) == "push_no_recipients":
                # Ни одна из подтверждённых подписок больше не принимает push:
                # не тратим следующие задания на заведомо мёртвые адреса.
                try:
                    revoked = revoke_devices(
                        PushDevice.objects.filter(
                            user_id=item.user_id,
                            subscription_id__in=subscription_ids,
                        )
                    )
                except Exception:
                    logger.exception(
                        "Не удалось отключить подписки после ответа провайдера",
                        user_id=item.user_id,
                    )
                else:
                    logger.warning(
                        "Подписки устройства отключены после ответа провайдера",
                        user_id=item.user_id,
                        count=revoked,
                    )
            raise
    else:
        message = EventEmail(
            event_key=item.event_key,
            capture_event=False,
            subject=item.title,
            body=f"{item.body}\n\n{item.url}",
            to=[item.user.email],
            connection=get_connection(timeout=SMTP_TIMEOUT),
        )
        if not message.send():
            _finish(item, "failed", "smtp_not_accepted")
            return
        provider_id = "smtp-accepted"
    _finish(item, "accepted", provider_id=provider_id)


def process_deliveries(*, limit: int = 100) -> int:
    """Обрабатывает ограниченное число заданий, безопасно при нескольких worker.

    Args:
        limit: Верхняя граница сетевых попыток за запуск.

    Returns:
        Число рассмотренных заданий; accepted означает только принятие транспортом.
    """
    if not center_enabled():
        return 0
    for campaign_id in Broadcast.objects.filter(status="queued").values_list(
        "pk", flat=True
    )[:limit]:
        try:
            expand_broadcast(campaign_id)
        except NotificationValidationError:
            logger.exception(
                "Не удалось сформировать рассылку", broadcast_id=campaign_id
            )
        except Exception:
            logger.exception("Сбой формирования рассылки", broadcast_id=campaign_id)
    _expire_interrupted_email(timezone.now())
    handled = 0
    for _ in range(limit):
        item = _claim()
        if item is None:
            break
        handled += 1
        try:
            _process(item)
        except NotificationValidationError as exc:
            _finish(item, "failed", _validation_code(item))
            logger.warning(
                "Задание отклонено политикой события",
                delivery_id=item.pk,
                event_key=item.event_key,
                error=str(exc),
            )
        except PushTransportError:
            _retry(item, "push_temporary_failure")
        except PushRejectedError as exc:
            _finish(item, "failed", str(exc))
        except (OSError, ValueError, smtplib.SMTPException):
            # SMTP-исключение может возникнуть после приёма письма: не дублировать.
            _finish(item, "uncertain", "transport_uncertain")
            logger.warning(
                "Неопределённый исход доставки",
                delivery_id=item.pk,
                channel=item.channel,
            )
        except Exception:
            # Неизвестный сбой одного задания не должен блокировать всю очередь.
            logger.exception(
                "Сбой обработки задания уведомлений",
                delivery_id=item.pk,
                event_key=item.event_key,
                channel=item.channel,
            )
            _retry(item, "worker_internal_error")
    return handled
