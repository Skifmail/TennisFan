"""CSRF-защищённые настройки и подтверждение браузерной подписки."""

import hashlib
import json
import secrets
import uuid
from typing import Final, cast

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.users.models import User

from .browser_identity import COOKIE_NAME, COOKIE_SALT, device_hash
from .device_binding import (
    CONFIRMATION_COOLDOWN,
    CONFIRMATION_LIFETIME,
    ConfirmationThrottled,
    DeviceBindingError,
    confirm_device,
    request_confirmation,
)
from .forms import PreferenceForm
from .infrastructure.push import PushRejectedError, PushTransportError, send_push
from .models import NotificationPreference, PushDevice
from .services import center_enabled, preferences

COOKIE_AGE_SECONDS: Final = 1209600
DEVICE_TOKEN_BYTES: Final = 32


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def notification_settings(request: HttpRequest) -> HttpResponse:
    """Сохраняет переключатели пользователя и направляет в единую панель.

    Args:
        request: Авторизованный запрос без принимаемого user_id.

    Returns:
        Редирект в ленту с раскрытыми настройками либо ответ об ошибке.
    """
    if not center_enabled():
        return HttpResponse("Центр уведомлений ещё не активирован.", status=503)
    target = reverse("notifications") + "?settings=1#notification-settings"
    if request.method == "GET":
        return redirect(target)
    push, email = preferences(cast(User, request.user).pk)
    form = PreferenceForm(
        request.POST if request.method == "POST" else None,
        initial={"push_enabled": push, "email_enabled": email},
    )
    if request.method == "POST" and form.is_valid():
        NotificationPreference.objects.update_or_create(
            user=request.user, defaults=form.cleaned_data
        )
        return redirect(target)
    return HttpResponse(
        "Не удалось сохранить настройки. Обновите страницу уведомлений.", status=400
    )


@login_required
@require_POST
@never_cache
@transaction.atomic
def notification_identity(request: HttpRequest) -> HttpResponse:
    """Выдаёт поколение привязки браузера, не используя JWT Web SDK.

    Args:
        request: Авторизованная сессия; клиентские user_id игнорируются.

    Returns:
        Поколение и ранее проверенная подписка без секретов подтверждения.
    """
    if not center_enabled() or not settings.ONESIGNAL_REST_API_KEY:
        return JsonResponse({"error": "push_not_configured"}, status=503)
    User.objects.select_for_update().get(pk=request.user.pk)
    token = request.get_signed_cookie(
        COOKIE_NAME, default="", salt=COOKIE_SALT
    ) or secrets.token_urlsafe(DEVICE_TOKEN_BYTES)
    device, _ = PushDevice.objects.select_for_update().get_or_create(
        device_hash=hashlib.sha256(token.encode()).hexdigest(),
        defaults={
            "user": request.user,
            "expires_at": request.session.get_expiry_date(),
        },
    )
    if device.user_id != request.user.pk or device.expires_at <= timezone.now():
        device.user = cast(User, request.user)
        device.external_id = uuid.uuid4()
        device.active = False
        device.subscription_id = None
    device.expires_at = request.session.get_expiry_date()
    device.save()
    response = JsonResponse(
        {
            "externalId": str(device.external_id),
            "verifiedSubscriptionId": (
                str(device.subscription_id)
                if device.active and device.subscription_id
                else None
            ),
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
def notification_device(request: HttpRequest) -> HttpResponse:
    """Отзывает устройство либо отправляет код только после явного действия.

    Args:
        request: JSON с поколением привязки, ID подписки и булевым active.

    Returns:
        Состояние либо безопасный код отказа; секрет отсутствует в JSON.
    """
    if not center_enabled() or not settings.ONESIGNAL_REST_API_KEY:
        return JsonResponse({"error": "disabled"}, status=503)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or not isinstance(data.get("active"), bool):
            return JsonResponse({"error": "invalid_active"}, status=400)
        uuid.UUID(str(data.get("externalId", "")))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_identity_or_json"}, status=400)
    try:
        state = request_confirmation(
            user=cast(User, request.user),
            device_hash=device_hash(request),
            generation=uuid.UUID(str(data["externalId"])),
            active=data["active"],
            subscription_id=str(data.get("subscriptionId", "")),
            enable_account=data.get("enableAccount") is True,
        )
    except ConfirmationThrottled:
        response = JsonResponse({"error": "confirmation_rate_limited"}, status=429)
        response["Retry-After"] = str(int(CONFIRMATION_COOLDOWN.total_seconds()))
        return response
    except DeviceBindingError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except IntegrityError:
        return JsonResponse({"error": "confirmation_conflict"}, status=409)
    if state.subscription_id:
        url = (
            settings.ONESIGNAL_SITE_ORIGIN.rstrip("/")
            + reverse("notification_confirm")
            + "#code="
            + state.code
        )
        try:
            send_push(
                subscription_ids=[state.subscription_id],
                title="Подтвердите уведомления",
                body="Нажмите, чтобы подключить уведомления TennisFan на этом устройстве.",
                url=url,
                key=str(uuid.uuid4()),
                ttl_seconds=int(CONFIRMATION_LIFETIME.total_seconds()),
            )
        except (PushRejectedError, PushTransportError):
            return JsonResponse({"error": "confirmation_unavailable"}, status=503)
    return JsonResponse(
        {
            "active": state.active,
            "pending": bool(state.subscription_id),
            "accountPushEnabled": state.account_push_enabled,
        },
        status=202 if state.subscription_id else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
@never_cache
def notification_confirm(request: HttpRequest) -> HttpResponse:
    """Подтверждает получение секрета в том же браузере и поколении аккаунта.

    Args:
        request: GET для просмотра; POST с CSRF для одноразового подтверждения.

    Returns:
        Изолированная HTML-страница без сторонних ресурсов либо отказ 409/503.
    """
    if not center_enabled():
        return HttpResponse(status=503)
    code = request.POST.get("code", "") if request.method == "POST" else ""
    complete = request.method == "POST" and request.POST.get("confirm") == "yes"
    valid = confirm_device(
        user=cast(User, request.user),
        device_hash=device_hash(request),
        code=code if request.method == "POST" else None,
        consume=complete,
    )
    complete = complete and valid
    response = render(
        request,
        "notifications/confirm.html",
        {
            "valid": valid,
            "complete": complete,
            "code": code if valid and not complete else "",
        },
        status=200 if valid else 409,
    )
    # HTTPS-форма требует same-origin Origin/Referer; no-referrer даёт Origin null.
    # Секрет остаётся во фрагменте и POST, сторонних ресурсов на странице нет.
    response["Referrer-Policy"] = "same-origin"
    response["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response
