"""Серверные формы приглашений без передачи контактных данных в браузер."""

from typing import Any, cast
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.users.models import User

from . import invitations, services
from .domain import MAX_DATABASE_ID, ChatError
from .models import ChatInvitation, ChatInvitationCampaign, ChatMembership


class InvitationRequestForm(forms.Form):
    """Проверяет выбранную аудиторию и безопасный числовой номер игрока.

    Attributes:
        audience: Одна из трёх поддерживаемых аудиторий.
        target_id: Номер адресата персонального приглашения.
    """

    audience = forms.ChoiceField(
        choices=[("person", "Игрок"), ("city", "Город"), ("platform", "Платформа")]
    )
    target_id = forms.IntegerField(
        required=False, min_value=1, max_value=MAX_DATABASE_ID
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def manage_invitations(request: HttpRequest, room_id: int) -> HttpResponse:
    """Показывает поиск, предпросмотр и журнал подтверждённых отправок.

    Args:
        request: Авторизованный запрос администратора или модератора.
        room_id: Его городская комната.

    Returns:
        Страница приглашений либо отказ без изменения данных.
    """
    user = cast(User, request.user)
    if not invitations.can_invite(user=user, room_id=room_id):
        return HttpResponse(
            "Приглашать могут администратор и модераторы этого чата.", status=403
        )
    member = services.require_member(user=user, room_id=room_id)
    context: dict[str, Any] = {
        "room": member.room,
        "memberships": list(services.my_memberships(user=user)),
        "query": request.GET.get("q", ""),
        "candidates": [],
        "campaigns": ChatInvitationCampaign.objects.filter(room_id=room_id)
        .select_related("sender")
        .order_by("-created_at")[: invitations.SEARCH_LIMIT],
    }
    try:
        if request.method == "POST":
            operation = request.POST.get("operation")
            if operation == "confirm":
                invitations.queue_invitation(
                    user=user, room_id=room_id, token=request.POST.get("token", "")
                )
                messages.success(
                    request,
                    "Приглашения поставлены в очередь. Уведомления и письма появятся после фоновой обработки.",
                )
                return redirect("chat:invitations", room_id=room_id)
            form = InvitationRequestForm(request.POST)
            if operation != "preview" or not form.is_valid():
                context["invitation_error"] = "Выберите способ приглашения и игрока."
            else:
                context["preview"] = invitations.preview_invitation(
                    user=user,
                    room_id=room_id,
                    audience=form.cleaned_data["audience"],
                    target_id=form.cleaned_data["target_id"],
                )
        else:
            context["candidates"] = invitations.search_invitees(
                user=user, room_id=room_id, query=context["query"]
            )
    except ChatError as exc:
        context["invitation_error"] = str(exc)
    return render(request, "chat/invitations.html", context)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def invitation_detail(request: HttpRequest, invitation_id: UUID) -> HttpResponse:
    """Открывает персональное приглашение, но не подтверждает вступление за игрока.

    Args:
        request: Запрос только получателя.
        invitation_id: Непредсказуемый идентификатор из уведомления или письма.

    Returns:
        Предложение перейти к согласиям либо сообщение о недоступности чата.
    """
    user = cast(User, request.user)
    if not user.is_active:
        return HttpResponse("Аккаунт недоступен.", status=403)
    invitation = get_object_or_404(
        ChatInvitation.objects.select_related("room__city"),
        pk=invitation_id,
        recipient=user,
    )
    if request.method == "POST":
        if request.POST.get("operation") != "decline":
            return HttpResponse("Неизвестное действие.", status=400)
        invitations.decline_invitation(user=user, invitation_id=invitation_id)
        return redirect("chat:invitation", invitation_id=invitation_id)
    member = ChatMembership.objects.filter(
        room=invitation.room, user=user, is_active=True
    ).exists()
    return render(
        request,
        "chat/invitation.html",
        {
            "invitation": invitation,
            "room": invitation.room,
            "joined": member,
            "available": invitation.room.is_active and invitation.room.is_created,
            "memberships": list(services.my_memberships(user=user)),
        },
    )


@never_cache
@require_http_methods(["GET", "POST"])
def invitation_email_settings(request: HttpRequest, token: str) -> HttpResponse:
    """Подтверждает отключение email-приглашений, не затрагивая сервисную почту.

    Args:
        request: GET показывает подтверждение, POST выполняет отключение.
        token: Подписанное право только отписаться.

    Returns:
        Подтверждение либо безопасная ошибка повреждённой ссылки.
    """
    context: dict[str, Any] = {"token": token, "saved": False}
    if request.method == "POST":
        try:
            invitations.unsubscribe_invitation_email(token=token)
        except ChatError:
            return HttpResponse("Ссылка отключения писем недействительна.", status=400)
        context["saved"] = True
    return render(request, "chat/invitation_email_settings.html", context)
