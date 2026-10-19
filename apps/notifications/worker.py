"""Пакетная доставка с арендой задания, повторной проверкой согласий и TTL."""

from datetime import timedelta
from typing import Final

from django.core.mail import get_connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from loguru import logger

from .domain.rules import channels_for
from .infrastructure.email import EventEmail
from .infrastructure.push import PushRejectedError, PushTransportError, send_push
from .models import Broadcast, Delivery, PushDevice
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


@transaction.atomic
def _claim() -> Delivery | None:
    now = timezone.now()
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
        return None
    item.status = "processing"
    item.attempts += 1
    item.available_at = now + LEASE
    item.save(update_fields=["status", "attempts", "available_at"])
    return item


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
        expand_broadcast(campaign_id)
    handled = 0
    for _ in range(limit):
        item = _claim()
        if item is None:
            break
        handled += 1
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
            continue
        try:
            if item.channel == "push":
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
                    continue
                provider_id = send_push(
                    subscription_ids=subscription_ids,
                    title=item.title,
                    body=item.body,
                    url=item.url,
                    key=str(item.key),
                )
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
                    continue
                provider_id = "smtp-accepted"
            _finish(item, "accepted", provider_id=provider_id)
        except PushTransportError:
            if item.attempts >= MAX_ATTEMPTS:
                _finish(item, "failed", "push_retry_limit")
            else:
                Delivery.objects.filter(
                    pk=item.pk, status="processing", attempts=item.attempts
                ).update(
                    status="queued",
                    error_code="push_temporary_failure",
                    available_at=timezone.now()
                    + timedelta(seconds=RETRY_BASE_SECONDS * 2 ** (item.attempts - 1)),
                )
        except PushRejectedError as exc:
            _finish(item, "failed", str(exc))
        except (OSError, ValueError):
            # SMTP-исключение может возникнуть после приёма письма: не дублировать.
            _finish(item, "uncertain", "transport_uncertain")
            logger.warning(
                "Неопределённый исход доставки",
                delivery_id=item.pk,
                channel=item.channel,
            )
    return handled
