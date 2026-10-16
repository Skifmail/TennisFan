"""Создание отдельных сообществ исключительно владельцем платформы."""

from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from loguru import logger

from apps.users.models import User

from .domain import MAX_CHAT_TITLE_LENGTH
from .models import ChatManagementEvent, ChatRoom

if TYPE_CHECKING:
    _CustomForm = forms.ModelForm[ChatRoom]
else:
    _CustomForm = forms.ModelForm


class CustomRoomForm(_CustomForm):
    """Проверяет название отдельного чата без изменения справочника городов.

    Attributes:
        title: Непустое название длиной до 120 символов.
    """

    title = forms.CharField(
        label="Название чата", max_length=MAX_CHAT_TITLE_LENGTH, strip=True
    )

    class Meta:
        model = ChatRoom
        fields = ("title",)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def create(request: HttpRequest) -> HttpResponse:
    """Показывает владельцу форму и создаёт отдельную открытую комнату.

    Args:
        request: Запрос активного суперпользователя платформы.

    Returns:
        Форма либо переход к личному подтверждению согласий создателя.
    """
    from .services import my_memberships

    user = cast(User, request.user)
    if not (user.is_active and user.is_staff and user.is_superuser):
        return HttpResponse(
            "Создавать отдельные чаты может только владелец платформы.", status=403
        )
    form = CustomRoomForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            room = form.save(commit=False)
            room.administrator = user
            room.save()
            ChatManagementEvent.objects.create(
                room=room, actor=user, target=user, action="created"
            )
        logger.info("Отдельный чат создан", room_id=room.pk, actor_id=user.pk)
        return redirect(reverse("chat:join") + f"?{room.join_query}")
    context: dict[str, Any] = {
        "form": form,
        "memberships": list(my_memberships(user=user)),
    }
    return render(
        request, "chat/create.html", context, status=400 if form.errors else 200
    )
