"""Восстановление гостевого доступа без входа в чужой аккаунт."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from loguru import logger

from apps.notifications.infrastructure.email import send_event_mail as send_mail

from .models import CampApplication, CampGuestAccess

LINK_LIFETIME = timedelta(minutes=30)


def _hash(token: str) -> str:
    """Хранит только хеш случайного секрета."""
    return hashlib.sha256(token.encode()).hexdigest()


def _allow(key: str, limit: int, seconds: int) -> bool:
    """Атомарно ограничивает число запросов, не сохраняя email открытым текстом в кэше."""
    cache.add(key, 0, timeout=seconds)
    try:
        return bool(cache.incr(key) <= limit)
    except ValueError:
        return False


def request_guest_access(*, email: str, address: str) -> None:
    """Отправляет одинаковый по внешнему результату запрос восстановления."""
    email = email.strip().lower()
    ip_key = salted_hmac("camp-recovery-ip", address).hexdigest()
    email_key = salted_hmac("camp-recovery-email", email).hexdigest()
    if not _allow(f"camp-recovery-ip:{ip_key}", 60, 3600) or not _allow(
        f"camp-recovery-email:{email_key}", 2, 600
    ):
        return
    ids = list(
        CampApplication.objects.filter(
            email__iexact=email, user__isnull=True
        ).values_list("pk", flat=True)[:100]
    )
    if not ids:
        return
    token = secrets.token_urlsafe(32)
    grant = CampGuestAccess.objects.create(
        email=email,
        token_hash=_hash(token),
        application_ids=[str(pk) for pk in ids],
        expires_at=timezone.now() + LINK_LIFETIME,
    )
    url = str(settings.SITE_URL).rstrip("/") + reverse("camps:restore", args=[token])
    try:
        sent = send_mail(
            "TennisFan · доступ к заявкам на кемп",
            f"Чтобы открыть гостевые заявки на кемпы, перейдите по ссылке и подтвердите доступ:\n{url}\n\nСсылка одноразовая и действует 30 минут. Если вы не запрашивали доступ, просто проигнорируйте письмо.",
            settings.DEFAULT_FROM_EMAIL,
            [email],
            event_key="camp.guest_access",
            capture_event=False,
            fail_silently=False,
        )
        if not sent:
            grant.delete()
    except Exception:
        grant.delete()
        logger.warning(
            "Не удалось отправить одноразовую ссылку кемпа; секрет не записывается в журнал"
        )


def valid_guest_link(token: str) -> bool:
    """Проверяет срок ссылки, не расходуя её при просмотре почтовым сканером."""
    return bool(
        len(token) <= 100
        and CampGuestAccess.objects.filter(
            token_hash=_hash(token), used_at__isnull=True, expires_at__gt=timezone.now()
        ).exists()
    )


@transaction.atomic
def consume_guest_link(token: str) -> list[str]:
    """Однократно подтверждает доступ только к исходным гостевым заявкам."""
    now = timezone.now()
    grant = (
        CampGuestAccess.objects.select_for_update()
        .filter(token_hash=_hash(token), used_at__isnull=True, expires_at__gt=now)
        .first()
        if len(token) <= 100
        else None
    )
    if grant is None:
        raise ValidationError("Ссылка больше не действует. Запросите новое письмо.")
    if not CampGuestAccess.objects.filter(
        pk=grant.pk, used_at__isnull=True, expires_at__gt=now
    ).update(used_at=now):
        raise ValidationError("Ссылка уже использована. Запросите новое письмо.")
    ids = CampApplication.objects.filter(
        pk__in=grant.application_ids, email__iexact=grant.email, user__isnull=True
    ).values_list("pk", flat=True)
    return [str(pk) for pk in ids]
