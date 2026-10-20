"""Контекст единой страницы управления уведомлениями без изменения согласий."""

from typing import cast

from django.http import HttpRequest
from django.utils import timezone

from apps.users.models import User

from .browser_identity import device_hash
from .forms import PreferenceForm
from .models import NotificationPreference, PushDevice
from .services import preferences


def settings_context(request: HttpRequest) -> dict[str, object]:
    """Готовит два переключателя и состояние раскрытия панели в личном кабинете.

    Args:
        request: Запрос авторизованного пользователя.

    Returns:
        Форма текущего пользователя без чтения переданного user_id и без записи в БД.
    """
    push, email = preferences(cast(User, request.user).pk)
    return {
        "notification_form": PreferenceForm(
            initial={"push_enabled": push, "email_enabled": email}
        ),
        "notification_settings_open": request.GET.get("settings") == "1",
        "notification_push_enabled": push,
    }


def should_invite(request: HttpRequest) -> bool:
    """Проверяет отсутствие подключённого текущего устройства, уважая явный отказ.

    Args:
        request: Авторизованный запрос главной страницы.

    Returns:
        Нужна ли временная подсказка без изменения согласий и истории.
    """
    user_id = cast(User, request.user).pk
    preference = NotificationPreference.objects.filter(user_id=user_id).first()
    if preference is not None and not preference.push_enabled:
        return False
    return not PushDevice.objects.filter(
        user_id=user_id,
        device_hash=device_hash(request),
        active=True,
        subscription_id__isnull=False,
        expires_at__gt=timezone.now(),
    ).exists()
