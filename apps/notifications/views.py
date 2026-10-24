"""CSRF-защищённые настройки и подтверждение браузерной подписки."""

import hashlib
import json
import secrets
import uuid
from typing import Final, cast

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST
from loguru import logger

from apps.users.models import User

from .browser_identity import COOKIE_NAME, COOKIE_SALT, device_hash
from .device_binding import (
    CONFIRMATION_COOLDOWN,
    CONFIRMATION_LIFETIME,
    ConfirmationThrottled,
    DeviceBindingError,
    confirm_device,
    request_confirmation,
    verify_device_code,
)
from .forms import PreferenceForm
from .infrastructure.push import PushRejectedError, PushTransportError, send_push
from .models import NotificationPreference, PushConfirmation, PushDevice
from .services import center_enabled, preferences

COOKIE_AGE_SECONDS: Final = 1209600
DEVICE_TOKEN_BYTES: Final = 32
TEST_PUSH_COOLDOWN_SECONDS: Final = 30
TEST_PUSH_CACHE_PREFIX: Final = "notifications:test-push:"
TEST_PUSH_TTL_SECONDS: Final = 600
TEST_PUSH_RETRY_SECONDS: Final = 15


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
    token = request.get_signed_cookie(
        COOKIE_NAME, default="", salt=COOKIE_SALT
    ) or secrets.token_urlsafe(DEVICE_TOKEN_BYTES)
    deadline = request.session.get_expiry_date()
    device, _ = PushDevice.objects.select_for_update().get_or_create(
        device_hash=hashlib.sha256(token.encode()).hexdigest(),
        defaults={
            "user": request.user,
            "expires_at": deadline,
        },
    )
    update_fields: list[str] = []
    if device.user_id != request.user.pk:
        device.user = cast(User, request.user)
        device.external_id = uuid.uuid4()
        device.active = False
        device.subscription_id = None
        device.expires_at = deadline
        update_fields = [
            "user",
            "external_id",
            "active",
            "subscription_id",
            "expires_at",
        ]
    elif device.expires_at <= timezone.now():
        # Обычно срок продлевает middleware на каждом авторизованном запросе;
        # здесь остаётся безопасный запасной путь без записи на каждый просмотр.
        device.expires_at = deadline
        update_fields = ["expires_at"]
    if update_fields:
        device.save(update_fields=update_fields)
    response = JsonResponse(
        {
            "externalId": str(device.external_id),
            "verifiedSubscriptionId": (
                str(device.subscription_id)
                if device.active and device.subscription_id
                else None
            ),
            "pushEnabled": preferences(cast(User, request.user).pk)[0],
            "pendingCode": PushConfirmation.objects.filter(
                device=device,
                generation=device.external_id,
                manual_code=True,
                expires_at__gt=timezone.now(),
            ).exists(),
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
def notification_device(
    request: HttpRequest, manual_code: bool = False
) -> HttpResponse:
    """Отзывает устройство либо отправляет код только после явного действия.

    Args:
        request: JSON с поколением привязки, ID подписки и булевым active.
        manual_code: Серверный маршрут запроса короткого кода.

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
            manual_code=manual_code,
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
        if manual_code:
            url = (
                settings.ONESIGNAL_SITE_ORIGIN.rstrip("/")
                + reverse("notifications")
                + "?settings=1#notification-settings"
            )
        try:
            send_push(
                subscription_ids=[state.subscription_id],
                title=(
                    "Код подключения TennisFan"
                    if manual_code
                    else "Подтвердите уведомления"
                ),
                body=(
                    f"Код подключения: {state.code}. Введите его в настройках уведомлений TennisFan. Код действует 5 минут. Никому не сообщайте его."
                    if manual_code
                    else "Нажмите, чтобы подключить уведомления TennisFan на этом устройстве."
                ),
                url=url,
                key=str(uuid.uuid4()),
                ttl_seconds=int(CONFIRMATION_LIFETIME.total_seconds()),
            )
        except (PushRejectedError, PushTransportError) as exc:
            logger.warning(
                "Не удалось отправить подтверждение push: {error_code}",
                error_code=str(exc),
            )
            response = JsonResponse({"error": "confirmation_unavailable"}, status=503)
            response["Retry-After"] = str(int(CONFIRMATION_COOLDOWN.total_seconds()))
            return response
    return JsonResponse(
        {
            "active": state.active,
            "pending": bool(state.subscription_id),
            "accountPushEnabled": state.account_push_enabled,
        },
        status=202 if state.subscription_id else 200,
    )


@login_required
@require_POST
@never_cache
def notification_test_push(request: HttpRequest) -> HttpResponse:
    """Отправляет тестовый push только на подтверждённое устройство пользователя.

    Args:
        request: JSON с ID подписки, уже подтверждённой коротким кодом.

    Returns:
        202 при приёме сообщения провайдером, безопасный код отказа иначе.
    """
    if not center_enabled() or not settings.ONESIGNAL_REST_API_KEY:
        return JsonResponse({"error": "disabled"}, status=503)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or not isinstance(
            data.get("subscriptionId"), str
        ):
            raise ValueError
        subscription_id = uuid.UUID(data["subscriptionId"])
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_subscription"}, status=400)
    device = PushDevice.objects.filter(
        user=request.user,
        active=True,
        expires_at__gt=timezone.now(),
        subscription_id=subscription_id,
    ).first()
    if device is None:
        return JsonResponse({"error": "device_not_found"}, status=409)
    limit_key = f"{TEST_PUSH_CACHE_PREFIX}{request.user.pk}"
    if not cache.add(limit_key, 1, timeout=TEST_PUSH_COOLDOWN_SECONDS):
        response = JsonResponse({"error": "test_rate_limited"}, status=429)
        response["Retry-After"] = str(TEST_PUSH_COOLDOWN_SECONDS)
        return response
    try:
        send_push(
            subscription_ids=[str(subscription_id)],
            title="Проверка уведомлений TennisFan",
            body=(
                "Тестовое уведомление. Если вы его видите, push на этом "
                "устройстве работают."
            ),
            url=(
                settings.ONESIGNAL_SITE_ORIGIN.rstrip("/")
                + reverse("notifications")
                + "?settings=1#notification-settings"
            ),
            key=str(uuid.uuid4()),
            ttl_seconds=TEST_PUSH_TTL_SECONDS,
        )
    except PushTransportError:
        logger.warning(
            "Тестовая push-доставка временно недоступна",
            user_id=request.user.pk,
        )
        response = JsonResponse({"error": "test_unavailable"}, status=503)
        response["Retry-After"] = str(TEST_PUSH_RETRY_SECONDS)
        return response
    except PushRejectedError as exc:
        if str(exc) == "push_no_recipients":
            # Устройство больше не принимает push: не оставляем мёртвую привязку.
            PushDevice.objects.filter(pk=device.pk).update(active=False)
            logger.warning(
                "Тестовое устройство отключено после отказа провайдера",
                user_id=request.user.pk,
            )
            return JsonResponse({"error": "device_unreachable"}, status=409)
        logger.warning(
            "Тестовая push-доставка отклонена: {error_code}", error_code=str(exc)
        )
        return JsonResponse({"error": "test_unavailable"}, status=503)
    return JsonResponse({"status": "accepted"}, status=202)


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


@login_required
@require_POST
@never_cache
def notification_code_verify(request: HttpRequest) -> HttpResponse:
    """Принимает короткий код только из CSRF-защищённого тела запроса.

    Args:
        request: JSON с кодом и текущим поколением исходного устройства.

    Returns:
        Состояние подключения либо безопасный отказ с ограничением подбора.
    """
    if not center_enabled():
        return JsonResponse({"error": "disabled"}, status=503)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or not isinstance(data.get("code"), str):
            return JsonResponse({"error": "invalid_json"}, status=400)
        generation = uuid.UUID(str(data.get("externalId", "")))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_identity_or_json"}, status=400)
    result = verify_device_code(
        user=cast(User, request.user),
        device_hash=device_hash(request),
        generation=generation,
        code=data["code"],
    )
    if result.active:
        return JsonResponse(
            {
                "active": True,
                "accountPushEnabled": True,
                "verifiedSubscriptionId": result.subscription_id,
            }
        )
    response = JsonResponse(
        {"error": result.error},
        status=(
            429
            if result.retry_after
            else (409 if result.error == "confirmation_expired" else 400)
        ),
    )
    if result.retry_after:
        response["Retry-After"] = str(result.retry_after)
    return response
