"""CSRF-защищённые настройки и серверная JWT-идентичность устройства."""

import hashlib
import json
import secrets
import uuid
from datetime import timedelta
from typing import Final, cast

import jwt
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.users.models import User

from .forms import PreferenceForm
from .models import NotificationPreference, PushDevice
from .services import center_enabled, preferences

COOKIE_NAME: Final = "notification_device"
COOKIE_SALT: Final = "notification-device"
COOKIE_AGE_SECONDS: Final = 1209600
JWT_LIFETIME: Final = timedelta(minutes=5)
DEVICE_TOKEN_BYTES: Final = 32


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def notification_settings(request: HttpRequest) -> HttpResponse:
    """Отдаёт и сохраняет два переключателя только текущего пользователя.

    Args:
        request: Авторизованный запрос без принимаемого user_id.

    Returns:
        Форма либо редирект после сохранения.
    """
    if not center_enabled():
        return HttpResponse("Центр уведомлений ещё не активирован.", status=503)
    push, email = preferences(cast(User, request.user).pk)
    form = PreferenceForm(
        request.POST if request.method == "POST" else None,
        initial={"push_enabled": push, "email_enabled": email},
    )
    if request.method == "POST" and form.is_valid():
        NotificationPreference.objects.update_or_create(
            user=request.user, defaults=form.cleaned_data
        )
        return redirect("notification_settings")
    return render(request, "notifications/settings.html", {"form": form})


@login_required
@require_POST
@never_cache
@transaction.atomic
def notification_identity(request: HttpRequest) -> HttpResponse:
    """Выдаёт короткий JWT только для серверного псевдонима этого браузера.

    Args:
        request: Сессия пользователя; user_id/external_id в POST игнорируются.

    Returns:
        Публичный псевдоним и краткоживущий JWT либо 503 до настройки ключа.
    """
    if not center_enabled() or not settings.ONESIGNAL_IDENTITY_PRIVATE_KEY:
        return JsonResponse({"error": "identity_not_configured"}, status=503)
    token = request.get_signed_cookie(
        COOKIE_NAME, default="", salt=COOKIE_SALT
    ) or secrets.token_urlsafe(DEVICE_TOKEN_BYTES)
    digest = hashlib.sha256(token.encode()).hexdigest()
    device, _ = PushDevice.objects.select_for_update().get_or_create(
        device_hash=digest,
        defaults={
            "user": request.user,
            "expires_at": request.session.get_expiry_date(),
        },
    )
    if device.user_id != request.user.pk:
        device.user = cast(User, request.user)
        device.external_id = uuid.uuid4()
        device.active = False
    device.expires_at = request.session.get_expiry_date()
    device.save()
    payload = {
        "iss": settings.ONESIGNAL_APP_ID,
        "exp": int((timezone.now() + JWT_LIFETIME).timestamp()),
        "identity": {"external_id": str(device.external_id)},
    }
    try:
        signed = jwt.encode(
            payload, settings.ONESIGNAL_IDENTITY_PRIVATE_KEY, algorithm="ES256"
        )
    except (ValueError, jwt.PyJWTError):
        return JsonResponse({"error": "identity_not_configured"}, status=503)
    response = JsonResponse(
        {
            "externalId": str(device.external_id),
            "jwt": signed,
            "pushEnabled": preferences(cast(User, request.user).pk)[0],
        }
    )
    response.set_signed_cookie(
        COOKIE_NAME,
        token,
        salt=COOKIE_SALT,
        max_age=COOKIE_AGE_SECONDS,
        secure=True,
        httponly=True,
        samesite="Lax",
    )
    return response


@login_required
@require_POST
@never_cache
@transaction.atomic
def notification_device(request: HttpRequest) -> HttpResponse:
    """Подтверждает или отзывает только собственное текущее устройство.

    Args:
        request: JSON с серверным externalId, булевым active и опциональным enableAccount.

    Returns:
        Подтверждение без ID устройства и без возможности чужой привязки.
    """
    if not center_enabled():
        return JsonResponse({"error": "disabled"}, status=503)
    token = request.get_signed_cookie(COOKIE_NAME, default="", salt=COOKIE_SALT) or ""
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=400)
    if not isinstance(data, dict) or not isinstance(data.get("active"), bool):
        return JsonResponse({"error": "invalid_active"}, status=400)
    try:
        alias = uuid.UUID(str(data.get("externalId", "")))
    except ValueError:
        return JsonResponse({"error": "no_identity"}, status=409)
    # Условный UPDATE повторно проверяет владельца после ожидания блокировки:
    # старая вкладка не может активировать уже перепривязанную строку.
    if not token:
        return JsonResponse({"error": "no_identity"}, status=409)
    updated = PushDevice.objects.filter(
        device_hash=hashlib.sha256(token.encode()).hexdigest(),
        user=request.user,
        external_id=alias,
        expires_at__gt=timezone.now(),
    ).update(active=data["active"], updated_at=timezone.now())
    if not updated:
        return JsonResponse({"error": "no_identity"}, status=409)
    if data["active"] and data.get("enableAccount") is True:
        NotificationPreference.objects.update_or_create(
            user=request.user, defaults={"push_enabled": True}
        )
    return JsonResponse(
        {
            "active": data["active"],
            "accountPushEnabled": preferences(cast(User, request.user).pk)[0],
        }
    )
