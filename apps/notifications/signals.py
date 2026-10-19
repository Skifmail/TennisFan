"""Отзыв серверной привязки устройства при выходе пользователя."""

import hashlib
import uuid
from typing import Any

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.http import HttpRequest

from .models import PushDevice
from .services import center_enabled


@receiver(user_logged_out)
@receiver(user_logged_in)
def revoke_device_on_logout(
    sender: object, request: HttpRequest | None, **kwargs: Any
) -> None:
    """Отзывает адрес при входе и выходе, включая отложенные запросы старых вкладок.

    Args:
        sender: Отправитель сигнала Django.
        request: Запрос выхода, если он доступен.
        **kwargs: Дополнительные параметры сигнала.
    """
    if request is not None and center_enabled():
        token = request.get_signed_cookie(
            "notification_device", default="", salt="notification-device"
        )
        if token:
            PushDevice.objects.filter(
                device_hash=hashlib.sha256(token.encode()).hexdigest()
            ).update(active=False, external_id=uuid.uuid4())
