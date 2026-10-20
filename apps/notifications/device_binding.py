"""Сценарии одноразового подтверждения подписки без зависимости от HTTP и SDK."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.users.models import User

from .domain.rules import NotificationError
from .models import NotificationPreference, PushCodeLimit, PushConfirmation, PushDevice
from .services import preferences

CONFIRMATION_LIFETIME: Final = timedelta(minutes=5)
CONFIRMATION_COOLDOWN: Final = timedelta(minutes=1)
CONFIRMATIONS_PER_MINUTE: Final = 5
TOKEN_BYTES: Final = 32
CODE_DIGITS: Final = 6
CODE_ATTEMPTS: Final = 5
CODE_ATTEMPT_WINDOW: Final = timedelta(minutes=5)


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
    manual_code: bool = False,
) -> DeviceState:
    """Отзывает адрес либо резервирует ограниченную отправку кода подтверждения.

    Args:
        user: Текущий авторизованный владелец.
        device_hash: Хэш подписанного серверного cookie.
        generation: Поколение, выданное сервером текущему браузеру.
        active: Разрешена ли локальная подписка браузера.
        subscription_id: Непроверенный ID SDK; не активируется без доставленного кода.
        enable_account: Явное нажатие пользователя, не фоновая синхронизация.
        manual_code: Использовать короткий код вместо ссылки.

    Returns:
        Состояние и, только для нового запроса, секрет для транспорта.

    Raises:
        DeviceBindingError: Устройство отозвано или адрес подписки некорректен.
        ConfirmationThrottled: Повторный запрос раньше установленного лимита.
    """
    # Фоновая синхронизация и отзыв не должны блокировать строку пользователя:
    # для явного включения важно сериализовать лимит подтверждений по аккаунту.
    if enable_account:
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
    code = (
        str(secrets.randbelow(10**CODE_DIGITS)).zfill(CODE_DIGITS)
        if manual_code
        else secrets.token_urlsafe(TOKEN_BYTES)
    )
    PushConfirmation.objects.create(
        device=device,
        subscription_id=address,
        generation=device.external_id,
        token_hash=(
            _code_digest(code, device.external_id)
            if manual_code
            else hashlib.sha256(code.encode()).hexdigest()
        ),
        manual_code=manual_code,
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
        manual_code=False,
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
        _activate_device(device, confirmation)
    return True


def _code_digest(code: str, generation: uuid.UUID) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"tennisfan:push-code:{generation}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class CodeVerification:
    """Возвращает итог ввода без секрета и без отката счётчика ошибок.

    Attributes:
        active: Устройство подключено.
        error: Безопасный код отказа.
        retry_after: Пауза при исчерпанном общем лимите.
        subscription_id: Адрес, фактически подтверждённый сервером.
    """

    active: bool = False
    error: str = "confirmation_invalid"
    retry_after: int = 0
    subscription_id: str = ""


@transaction.atomic
def verify_device_code(
    *, user: User, device_hash: str, generation: uuid.UUID, code: str
) -> CodeVerification:
    """Подтверждает короткий код с общим для аккаунта лимитом подбора.

    Args:
        user: Авторизованный владелец.
        device_hash: Хэш подписанного cookie исходного браузера.
        generation: Текущее поколение привязки.
        code: Код из тела защищённого POST-запроса.

    Returns:
        Результат без секрета; ошибочные вводы сохраняются при отказе.
    """
    User.objects.select_for_update().get(pk=user.pk)
    now = timezone.now()
    device = (
        PushDevice.objects.select_for_update()
        .filter(
            user=user,
            device_hash=device_hash,
            external_id=generation,
            expires_at__gt=now,
        )
        .first()
    )
    if device is None:
        return CodeVerification(error="confirmation_expired")
    confirmation = (
        PushConfirmation.objects.select_for_update()
        .filter(
            device=device, generation=generation, manual_code=True, expires_at__gt=now
        )
        .first()
    )
    if confirmation is None:
        return CodeVerification(error="confirmation_expired")
    limit, _ = PushCodeLimit.objects.get_or_create(
        user=user, defaults={"resets_at": now + CODE_ATTEMPT_WINDOW}
    )
    if limit.resets_at <= now:
        limit.failures = 0
        limit.resets_at = now + CODE_ATTEMPT_WINDOW
        limit.save(update_fields=["failures", "resets_at"])
    retry_after = max(1, int((limit.resets_at - now).total_seconds()) + 1)
    if limit.failures >= CODE_ATTEMPTS:
        return CodeVerification(
            error="confirmation_attempts_exhausted", retry_after=retry_after
        )
    valid = len(code) == CODE_DIGITS and code.isascii() and code.isdecimal()
    if not valid or not hmac.compare_digest(
        confirmation.token_hash, _code_digest(code, generation)
    ):
        limit.failures += 1
        limit.save(update_fields=["failures"])
        return CodeVerification(
            error=(
                "confirmation_attempts_exhausted"
                if limit.failures >= CODE_ATTEMPTS
                else "confirmation_invalid"
            ),
            retry_after=retry_after if limit.failures >= CODE_ATTEMPTS else 0,
        )
    _activate_device(device, confirmation)
    return CodeVerification(
        active=True, error="", subscription_id=str(device.subscription_id)
    )


def _activate_device(device: PushDevice, confirmation: PushConfirmation) -> None:
    PushDevice.objects.filter(subscription_id=confirmation.subscription_id).exclude(
        pk=device.pk
    ).update(active=False, subscription_id=None)
    device.subscription_id = confirmation.subscription_id
    device.active = True
    device.save(update_fields=["subscription_id", "active", "updated_at"])
    confirmation.delete()
    NotificationPreference.objects.update_or_create(
        user=device.user, defaults={"push_enabled": True}
    )
