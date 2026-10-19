"""Сценарии одноразового подтверждения подписки без зависимости от HTTP и SDK."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.users.models import User

from .domain.rules import NotificationError
from .models import NotificationPreference, PushConfirmation, PushDevice
from .services import preferences

CONFIRMATION_LIFETIME: Final = timedelta(minutes=5)
CONFIRMATION_COOLDOWN: Final = timedelta(minutes=1)
CONFIRMATIONS_PER_MINUTE: Final = 5
TOKEN_BYTES: Final = 32


class DeviceBindingError(NotificationError):
    """Поколение устройства отсутствует или уже отозвано.

    Attributes:
        args: Безопасный код отказа, без адресов и секретов.
    """


class ConfirmationThrottled(DeviceBindingError):
    """Достигнут лимит запросов подтверждения.

    Attributes:
        args: Безопасный код ограничения.
    """


@dataclass(frozen=True)
class DeviceState:
    """Результат сценария с внутренним секретом только для транспорта.

    Attributes:
        active: Подписка ранее подтверждена и не отозвана.
        account_push_enabled: Глобальное согласие аккаунта.
        subscription_id: Адрес для отправки нового подтверждения, иначе пустая строка.
        code: Новый секрет исключительно для отправки через OneSignal.
    """

    active: bool
    account_push_enabled: bool
    subscription_id: str = ""
    code: str = ""


@transaction.atomic
def request_confirmation(
    *,
    user: User,
    device_hash: str,
    generation: uuid.UUID,
    active: bool,
    subscription_id: str,
    enable_account: bool,
) -> DeviceState:
    """Отзывает адрес либо резервирует ограниченную отправку кода подтверждения.

    Args:
        user: Текущий авторизованный владелец.
        device_hash: Хэш подписанного серверного cookie.
        generation: Поколение, выданное сервером текущему браузеру.
        active: Разрешена ли локальная подписка браузера.
        subscription_id: Непроверенный ID SDK; не активируется без доставленного кода.
        enable_account: Явное нажатие пользователя, не фоновая синхронизация.

    Returns:
        Состояние и, только для нового запроса, секрет для транспорта.

    Raises:
        DeviceBindingError: Устройство отозвано или адрес подписки некорректен.
        ConfirmationThrottled: Повторный запрос раньше установленного лимита.
    """
    User.objects.select_for_update().get(pk=user.pk)
    device = (
        PushDevice.objects.select_for_update()
        .filter(
            device_hash=device_hash,
            user=user,
            external_id=generation,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if device is None:
        raise DeviceBindingError("no_identity")
    push_enabled = preferences(user.pk)[0]
    if not active:
        device.active = False
        device.subscription_id = None
        device.save(update_fields=["active", "subscription_id", "updated_at"])
        # Сохраняем requested_at, чтобы отказ не обходил ограничение частоты.
        PushConfirmation.objects.filter(device=device).update(
            token_hash=secrets.token_hex(TOKEN_BYTES), expires_at=timezone.now()
        )
        return DeviceState(False, push_enabled)
    try:
        address = uuid.UUID(subscription_id)
    except ValueError as exc:
        raise DeviceBindingError("invalid_subscription") from exc
    if device.active and device.subscription_id == address:
        if enable_account:
            NotificationPreference.objects.update_or_create(
                user=user, defaults={"push_enabled": True}
            )
            push_enabled = True
        return DeviceState(True, push_enabled)
    if device.active:
        device.active = False
        device.subscription_id = None
        device.save(update_fields=["active", "subscription_id", "updated_at"])
    if not enable_account:
        return DeviceState(False, push_enabled)
    now = timezone.now()
    recent = PushConfirmation.objects.filter(
        requested_at__gt=now - CONFIRMATION_COOLDOWN
    )
    if (
        recent.filter(Q(device=device) | Q(subscription_id=address)).exists()
        or recent.filter(device__user=user).count() >= CONFIRMATIONS_PER_MINUTE
    ):
        raise ConfirmationThrottled("confirmation_rate_limited")
    PushConfirmation.objects.filter(
        Q(device=device) | Q(subscription_id=address)
    ).delete()
    code = secrets.token_urlsafe(TOKEN_BYTES)
    PushConfirmation.objects.create(
        device=device,
        subscription_id=address,
        generation=device.external_id,
        token_hash=hashlib.sha256(code.encode()).hexdigest(),
        expires_at=now + CONFIRMATION_LIFETIME,
        requested_at=now,
    )
    return DeviceState(False, push_enabled, str(address), code)


@transaction.atomic
def confirm_device(
    *,
    user: User,
    device_hash: str,
    code: str | None,
    consume: bool,
) -> bool:
    """Проверяет код и атомарно активирует только исходный браузер и аккаунт.

    Args:
        user: Авторизованный владелец браузера.
        device_hash: Хэш подписанного cookie.
        code: Секрет из тела POST; None означает только просмотр формы.
        consume: Подтверждённое действие с CSRF-проверкой на транспортном слое.

    Returns:
        Есть ли действующее подтверждение; код потребляется не более одного раза.
    """
    User.objects.select_for_update().get(pk=user.pk)
    device = (
        PushDevice.objects.select_for_update()
        .filter(
            device_hash=device_hash,
            user=user,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if device is None:
        return False
    confirmations = PushConfirmation.objects.select_for_update().filter(
        device=device,
        generation=device.external_id,
        expires_at__gt=timezone.now(),
    )
    if code is not None:
        if not code:
            return False
        confirmations = confirmations.filter(
            token_hash=hashlib.sha256(code.encode()).hexdigest()
        )
    confirmation = confirmations.first()
    if confirmation is None or (consume and code is None):
        return False
    if consume:
        PushDevice.objects.filter(subscription_id=confirmation.subscription_id).exclude(
            pk=device.pk
        ).update(active=False, subscription_id=None)
        device.subscription_id = confirmation.subscription_id
        device.active = True
        device.save(update_fields=["subscription_id", "active", "updated_at"])
        confirmation.delete()
        NotificationPreference.objects.update_or_create(
            user=user, defaults={"push_enabled": True}
        )
    return True
