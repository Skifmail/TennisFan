"""Вход, регистрация и управление привязками социальных аккаунтов."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from datetime import timedelta
from typing import Any, cast
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from loguru import logger

from apps.core.redirects import get_safe_next_url

from .models import Player, SocialAuthFlow, SocialIdentity, User
from .social_avatar import import_social_avatar
from .social_connections import connection_providers
from .social_consent import (
    SESSION_NONCE,
    SocialConsentForm,
    acceptance_snapshot,
    build_form,
    consent_context,
    load_documents,
    record_social_acceptance,
    validate_form,
)
from .social_oauth import SocialAuthError, fetch_identity, get_provider
from .social_onboarding import register_social_user
from .views import (
    _registration_allowed,
    _send_registration_notifications,
)

SESSION_FLOW = "social_auth_flow"


def _flow(request: HttpRequest, *, lock: bool = False) -> SocialAuthFlow:
    """Найти незавершённый сеанс, связанный с браузером и сроком действия."""
    queryset = SocialAuthFlow.objects.all()
    if lock:
        queryset = queryset.select_for_update()
    flow = queryset.filter(
        state=request.session.get(SESSION_FLOW, ""), expires_at__gt=timezone.now()
    ).first()
    if flow is None or (not flow.login_only and not flow.legal_acceptance):
        raise SocialAuthError
    return cast(SocialAuthFlow, flow)


def _failure(request: HttpRequest, message: str, next_url: str = "") -> HttpResponse:
    """Показать понятную ошибку без секретов провайдера."""
    messages.error(request, message)
    return redirect(
        next_url
        or get_safe_next_url(request)
        or ("social_connections" if request.user.is_authenticated else "login")
    )


def _confirm_consent(
    request: HttpRequest,
    provider: str,
    external_provider: str,
    *,
    connect: bool,
    next_url: str,
) -> tuple[HttpResponse | None, dict[str, Any]]:
    """Подтвердить документы только для регистрации или явной привязки."""
    config = get_provider(provider)
    try:
        documents = load_documents()
    except (OSError, UnicodeError, ValueError):
        logger.exception("Документы для социального входа недоступны")
        return _failure(request, "Документы временно недоступны. Попробуйте позже."), {}
    context = consent_context(
        request,
        provider=provider,
        external_provider=external_provider,
        connect=connect,
        next_url=next_url,
        documents=documents,
    )
    submitted = request.method == "POST" and "token" in request.POST
    form = SocialConsentForm(request.POST) if submitted else build_form(context)
    if not submitted or not validate_form(form, context):
        if submitted and form.non_field_errors():
            errors = list(form.non_field_errors())
            form = SocialConsentForm({"token": build_form(context)["token"].value()})
            form.is_valid()
            for error in errors:
                form.add_error(None, error)
        return (
            render(
                request,
                "users/social_consent.html",
                {
                    "form": form,
                    "provider": provider,
                    "provider_label": {"ok_ru": "Одноклассники", "mail_ru": "Mail"}.get(
                        external_provider, config.label
                    ),
                    "external_provider": external_provider,
                    "connect": connect,
                    "register": not connect,
                    "next_url": next_url,
                },
                status=400 if submitted else 200,
            ),
            {},
        )
    return None, acceptance_snapshot(request, documents, context)


@never_cache
@require_GET
def registration_consent(request: HttpRequest) -> HttpResponse:
    """Предложить регистрацию, если проверенный внешний аккаунт ещё не привязан."""
    if request.user.is_authenticated:
        return redirect("home")
    try:
        flow = _flow(request)
        if not flow.login_only or not flow.identity.get("subject"):
            raise SocialAuthError
    except SocialAuthError:
        return _failure(request, "Сеанс завершён. Начните вход через соцсеть заново.")
    response, _ = _confirm_consent(
        request,
        flow.provider,
        flow.external_provider,
        connect=False,
        next_url=flow.next_url,
    )
    assert response is not None
    return response


@never_cache
@require_POST
def start(request: HttpRequest, provider: str) -> HttpResponse:
    """Начать вход или привязку с защитой CSRF, state и PKCE S256."""
    config = get_provider(provider)
    external_provider = request.POST.get("external_provider", "")
    if external_provider and (
        provider != "vk" or external_provider not in ("ok_ru", "mail_ru")
    ):
        raise Http404
    connect = request.POST.get("process") == "connect"
    if connect and not request.user.is_authenticated:
        return redirect("login")
    if request.user.is_authenticated and not connect:
        return redirect("social_connections")
    if not _registration_allowed(request):
        return _failure(request, "Слишком много попыток. Попробуйте позже.")
    next_url = get_safe_next_url(request)[:2000]
    login_only = (
        not connect
        and request.POST.get("process") != "signup"
        and "token" not in request.POST
    )
    snapshot: dict[str, Any] = {}
    if not login_only:
        response, snapshot = _confirm_consent(
            request, provider, external_provider, connect=connect, next_url=next_url
        )
        if response is not None:
            return response
    SocialAuthFlow.objects.filter(expires_at__lte=timezone.now()).delete()
    old_state = request.session.pop(SESSION_FLOW, "")
    SocialAuthFlow.objects.filter(state=old_state).delete()
    verifier = secrets.token_urlsafe(64)
    flow = SocialAuthFlow.objects.create(
        state=secrets.token_urlsafe(32),
        provider=provider,
        code_verifier=verifier,
        redirect_uri=config.redirect_uri,
        next_url=next_url,
        legal_acceptance=snapshot,
        login_only=login_only,
        external_provider=external_provider,
        connect_user_id=request.user.pk if connect else None,
        expires_at=timezone.now() + timedelta(minutes=15),
    )
    request.session[SESSION_FLOW] = flow.state
    request.session.pop(SESSION_NONCE, None)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    query = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": (
            ("login:info" if provider == "yandex" else "vkid.personal_info")
            if login_only
            else config.scope
        ),
        "state": flow.state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if external_provider:
        query["provider"] = external_provider
        query["prompt"] = "login"
    if config.optional_scope and not login_only:
        query["optional_scope"] = config.optional_scope
    if provider == "yandex" and not login_only:
        query["force_confirm"] = "yes"
    return redirect(f"{config.authorize_url}?{urlencode(query)}")


@never_cache
@require_GET
def callback(request: HttpRequest, provider: str) -> HttpResponse:
    """Проверить ответ сервиса и однократно использовать серверный сеанс."""
    config = get_provider(provider)
    failure_target = ""
    try:
        with transaction.atomic():
            flow = _flow(request, lock=True)
            if flow.connect_user_id:
                failure_target = flow.next_url
            if (
                flow.provider != provider
                or flow.redirect_uri != config.redirect_uri
                or flow.identity
                or not constant_time_compare(flow.state, request.GET.get("state", ""))
            ):
                raise SocialAuthError
            if flow.connect_user_id:
                if (
                    not request.user.is_authenticated
                    or request.user.pk != flow.connect_user_id
                    or not request.user.is_active
                ):
                    raise SocialAuthError
            elif request.user.is_authenticated:
                raise SocialAuthError
            code = request.GET.get("code", "")
            if request.GET.get("error") or not code:
                raise SocialAuthError
            identity = fetch_identity(
                config,
                code=code,
                verifier=flow.code_verifier,
                state=flow.state,
                device_id=request.GET.get("device_id", ""),
                subject_only=flow.login_only,
            )
            linked = (
                SocialIdentity.objects.select_related("user")
                .filter(provider=provider, subject=identity["subject"])
                .first()
            )
            if flow.connect_user_id:
                if flow.login_only:
                    raise SocialAuthError
                user = User.objects.select_for_update().get(pk=flow.connect_user_id)
                if linked and linked.user_id != user.pk:
                    raise SocialAuthError
                if not linked:
                    SocialIdentity.objects.create(
                        user=user, provider=provider, subject=identity["subject"]
                    )
                record_social_acceptance(flow, user)
                target = flow.next_url
                flow.delete()
                request.session.pop(SESSION_FLOW, None)
                messages.success(request, f"Аккаунт {config.label} подключён.")
                return redirect(target or "social_connections")
            if linked:
                if not linked.user.is_active:
                    raise SocialAuthError
                if not flow.login_only:
                    record_social_acceptance(flow, linked.user)
                target = flow.next_url
                flow.delete()
                request.session.pop(SESSION_FLOW, None)
                login(
                    request,
                    linked.user,
                    backend="django.contrib.auth.backends.ModelBackend",
                )
                return redirect(target or "home")
            if flow.login_only:
                # Не сохраняем контакты, фото и остальные поля до отдельного согласия.
                flow.identity = {"subject": identity["subject"]}
                flow.save(update_fields=["identity"])
                return redirect("social_registration_consent")
            identity["email"] = identity.get("email", "").strip().lower()
            if _matching_users(identity):
                flow.identity = identity
                flow.save(update_fields=["identity"])
                return redirect("social_signup")
            return _complete_signup(request, flow, identity)
    except (SocialAuthError, IntegrityError):
        # Удаление вне откатившейся транзакции делает отказ и ошибку одноразовыми.
        SocialAuthFlow.objects.filter(
            state=request.session.pop(SESSION_FLOW, "")
        ).delete()
        logger.warning("Социальный вход отклонён: provider={}", provider)
        return _failure(
            request,
            "Не удалось подтвердить вход. Попробуйте снова. Если соцсеть уже подключена к другому профилю, используйте его.",
            next_url=failure_target,
        )
    return redirect("social_signup")


def _phone_key(value: str) -> str:
    """Сравнить телефоны без оформления, учитывая российскую восьмёрку."""
    digits = re.sub(r"[^0-9]", "", value)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits if len(digits) >= 10 else ""


def _matching_users(identity: dict[str, str]) -> list[User]:
    """Найти совпадения контактов без автоматической привязки."""
    email = identity.get("email", "").strip().lower()
    phone = _phone_key(identity.get("phone", ""))
    matches: dict[int, User] = {}
    if email:
        for user in User.objects.filter(email__iexact=email):
            matches[user.pk] = user
    if phone:
        for user in User.objects.exclude(phone="").iterator():
            if _phone_key(user.phone or "") == phone:
                matches[user.pk] = user
    return list(matches.values())


@never_cache
@require_http_methods(["GET", "POST"])
def signup(request: HttpRequest) -> HttpResponse:
    """Предложить владельцу найденного профиля войти прежним способом."""
    if request.user.is_authenticated:
        return redirect("home")
    try:
        with transaction.atomic():
            flow = _flow(request, lock=True)
            get_provider(flow.provider)
            if flow.login_only or not flow.identity or flow.connect_user_id:
                raise SocialAuthError
            identity = dict(flow.identity)
            matches = _matching_users(identity)
            if request.method == "POST":
                choice = request.POST.get("choice")
                if choice == "yes":
                    flow.delete()
                    request.session.pop(SESSION_FLOW, None)
                    messages.info(
                        request,
                        "Войдите привычным способом. Затем откройте «Редактирование профиля → Подключённые соцсети» и привяжите выбранную соцсеть. После этого через неё можно будет входить.",
                    )
                    return redirect("login")
                if choice == "no":
                    # Занятые контакты не переносим в новый профиль.
                    for user in matches:
                        if (
                            user.email
                            and user.email.lower() == identity.get("email", "").lower()
                        ):
                            identity["email"] = ""
                        if _phone_key(user.phone or "") and _phone_key(
                            user.phone or ""
                        ) == _phone_key(identity.get("phone", "")):
                            identity["phone"] = ""
                    return _complete_signup(request, flow, identity)
            players = {
                player.user_id: player
                for player in Player.objects.filter(user__in=matches).select_related(
                    "user"
                )
            }
            profiles: list[dict[str, object]] = []
            for user in matches:
                player = players.get(user.pk)
                display_name = user.get_display_name().strip()
                if not display_name or display_name == (user.email or ""):
                    display_name = "Пользователь TennisFan"
                profiles.append(
                    {
                        "display_name": display_name,
                        "avatar": player.avatar if player else None,
                        "city": player.city if player else "",
                        "level": player.get_skill_level_display() if player else "",
                        "rating": player.total_points if player else 0,
                    }
                )
            return render(
                request,
                "users/social_account_match.html",
                {"matches": matches, "profiles": profiles},
            )
    except (SocialAuthError, IntegrityError):
        SocialAuthFlow.objects.filter(
            state=request.session.pop(SESSION_FLOW, "")
        ).delete()
        return _failure(request, "Сеанс завершён. Начните вход через соцсеть заново.")


def _complete_signup(
    request: HttpRequest, flow: SocialAuthFlow, identity: dict[str, str]
) -> HttpResponse:
    """Завершить создание аккаунта и погасить сеанс."""
    if flow.login_only or not flow.legal_acceptance:
        raise SocialAuthError
    provider = flow.provider
    user, player = register_social_user(request, provider, identity)
    record_social_acceptance(flow, user)
    target = flow.next_url
    avatar_url = identity.get("avatar_url", "")
    flow.delete()
    request.session.pop(SESSION_FLOW, None)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    transaction.on_commit(lambda: _send_registration_notifications(user, player))
    if avatar_url:
        transaction.on_commit(
            lambda: import_social_avatar(player.pk, provider, avatar_url)
        )
    messages.success(
        request,
        "Добро пожаловать! Недостающие данные можно заполнить в профиле.",
    )
    return redirect(target or "home")


@login_required
@never_cache
@require_GET
def connections(request: HttpRequest) -> HttpResponse:
    """Показать подключённые сервисы и доступные способы привязки."""

    return render(
        request,
        "users/social_connections.html",
        {
            "providers": connection_providers(cast(User, request.user)),
            "social_password_available": cast(User, request.user).has_usable_password(),
        },
    )


@login_required
@never_cache
@require_POST
def disconnect(request: HttpRequest, provider: str) -> HttpResponse:
    """Отвязать сервис, сохранив хотя бы один доступный способ входа."""
    if provider not in ("yandex", "vk"):
        raise Http404
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        identities = list(SocialIdentity.objects.filter(user=user))
        # Даже выключенный провайдер разрешено отвязать, но он не считается запасным входом.
        alternative = user.has_usable_password()
        for identity in identities:
            if identity.provider != provider:
                try:
                    get_provider(identity.provider)
                except Http404:
                    continue
                alternative = True
        if not alternative:
            messages.error(
                request,
                "Нельзя отключить последний способ входа. Сначала подключите другую соцсеть или задайте пароль в личном кабинете после подтверждения почты.",
            )
        else:
            SocialIdentity.objects.filter(user=user, provider=provider).delete()
            messages.success(request, "Соцсеть отключена.")
    return redirect(get_safe_next_url(request) or "social_connections")


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def set_password(request: HttpRequest) -> HttpResponse:
    """Добавить пароль социальному аккаунту после подтверждения его почты."""
    user = cast(User, request.user)
    if user.has_usable_password():
        return redirect("password_change")
    if not user.email_verified:
        return _failure(
            request,
            "Сначала подтвердите email по ссылке из письма. Затем можно будет задать пароль.",
        )
    form = SetPasswordForm(user, request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(pk=user.pk)
            if locked_user.has_usable_password():
                return redirect("password_change")
            if not locked_user.email_verified:
                return _failure(request, "Сначала подтвердите email.")
            locked_user.set_password(form.cleaned_data["new_password1"])
            locked_user.save(update_fields=["password"])
        update_session_auth_hash(request, locked_user)
        messages.success(
            request, "Пароль задан. Теперь можно входить по email и паролю."
        )
        logger.info("Социальный аккаунт: добавлен пароль user={}", user.pk)
        return redirect("social_connections")
    return render(request, "users/social_set_password.html", {"form": form})
