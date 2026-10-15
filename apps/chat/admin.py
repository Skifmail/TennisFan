"""Управление городским чатом, его участниками и перепиской из одной карточки."""

from __future__ import annotations

from typing import Any, Final, cast

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Model, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse

from .domain import MAX_DATABASE_ID
from .models import ChatMembership, ChatMessage, ChatReport, ChatRoom
from .realtime import publish_room_change

ADMIN_PAGE_SIZE: Final = 25
MESSAGE_PREVIEW_LENGTH: Final = 240


def _can_view_rooms(request: HttpRequest) -> bool:
    return cast(
        bool,
        request.user.has_perm("chat.view_chatroom")
        or request.user.has_perm("chat.change_chatroom"),
    )


def _can_view(request: HttpRequest, model: str) -> bool:
    return cast(
        bool,
        request.user.has_perm(f"chat.view_{model}")
        or request.user.has_perm(f"chat.change_{model}"),
    )


def _room_url(room: ChatRoom, *, members: bool = False) -> str:
    url = cast(str, reverse("admin:chat_chatroom_change", args=[room.pk]))
    return url + ("#chat-room-members" if members else "")


def _child_room(obj: Model) -> ChatRoom:
    if not isinstance(obj, (ChatMembership, ChatMessage)):
        raise Http404("Объект не относится к городскому чату.")
    return cast(ChatRoom, obj.room)


def _requested_room(request: HttpRequest) -> ChatRoom:
    try:
        room_id = int(request.GET.get("room_id", ""))
    except ValueError as exc:
        raise Http404("Выберите городской чат.") from exc
    if not 0 < room_id <= MAX_DATABASE_ID:
        raise Http404("Городской чат не найден.")
    return cast(
        ChatRoom,
        get_object_or_404(ChatRoom.objects.select_related("city"), pk=room_id),
    )


@admin.register(ChatRoom)
class ChatRoomAdmin(admin.ModelAdmin):
    """Объединяет настройки комнаты, сообщения и управление её участниками.

    Attributes:
        change_form_template: Шаблон карточки с двумя вложенными блоками.
    """

    list_display = ("city", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("city__name", "city__region")
    autocomplete_fields = ("city",)
    change_form_template = "admin/chat/room_change_form.html"

    def get_queryset(self, request: HttpRequest) -> QuerySet[ChatRoom]:
        """Загружает города вместе с комнатами без дополнительных запросов.

        Args:
            request: Запрос администратора.

        Returns:
            Комнаты с населёнными пунктами.
        """
        return super().get_queryset(request).select_related("city")

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, Any],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: ChatRoom | None = None,
    ) -> HttpResponse:
        """Добавляет независимые страницы участников и сообщений выбранной комнаты.

        Args:
            request: Запрос администратора.
            context: Контекст стандартной формы.
            add: Создаётся ли новая комната.
            change: Открыта ли существующая комната.
            form_url: Адрес сохранения формы.
            obj: Выбранная комната либо None при создании.

        Returns:
            Карточка с данными, разрешёнными правами пользователя.
        """
        context.update(
            chat_messages_page=None,
            chat_members_page=None,
            chat_can_add_member=request.user.has_perm("chat.add_chatmembership"),
            chat_can_change_member=request.user.has_perm("chat.change_chatmembership"),
            chat_can_delete_member=request.user.has_perm("chat.delete_chatmembership"),
            chat_message_preview_length=MESSAGE_PREVIEW_LENGTH,
        )
        if obj is not None:
            if _can_view(request, "chatmessage"):
                context["chat_messages_page"] = Paginator(
                    obj.messages.select_related("author").order_by("-pk"),
                    ADMIN_PAGE_SIZE,
                ).get_page(request.GET.get("messages_page"))
            if _can_view(request, "chatmembership"):
                context["chat_members_page"] = Paginator(
                    obj.memberships.select_related("user").order_by(
                        "-is_active", "user__last_name", "user__first_name", "pk"
                    ),
                    ADMIN_PAGE_SIZE,
                ).get_page(request.GET.get("members_page"))
        return super().render_change_form(request, context, add, change, form_url, obj)


class _RoomChildAdmin(admin.ModelAdmin):
    """Сохраняет дочерние формы доступными только в контексте городского чата."""

    change_form_template = "admin/chat/child_change_form.html"

    def get_model_perms(self, request: HttpRequest) -> dict[str, bool]:
        """Скрывает дочернюю модель из главного меню, сохраняя её разрешения.

        Args:
            request: Запрос администратора.

        Returns:
            Пустой набор пунктов меню.
        """
        return {}

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        """Перенаправляет старые глобальные списки к выбору городского чата.

        Args:
            request: Запрос администратора.
            extra_context: Дополнительный контекст стандартного списка.

        Returns:
            Переход к списку городских чатов.

        Raises:
            PermissionDenied: Нет права просматривать городские чаты.
        """
        if not _can_view_rooms(request):
            raise PermissionDenied
        return HttpResponseRedirect(reverse("admin:chat_chatroom_changelist"))

    def get_queryset(self, request: HttpRequest) -> QuerySet[Model]:
        """Загружает родительский чат и город вместе с дочерним объектом.

        Args:
            request: Запрос администратора.

        Returns:
            Дочерние объекты с родительскими комнатами.
        """
        return super().get_queryset(request).select_related("room__city")

    def get_object(
        self, request: HttpRequest, object_id: str, from_field: str | None = None
    ) -> Model | None:
        """Проверяет соответствие объекта городу, указанному в адресе формы.

        Args:
            request: Запрос администратора.
            object_id: Идентификатор дочернего объекта.
            from_field: Поле поиска объекта.

        Returns:
            Найденный объект либо None.

        Raises:
            Http404: Родительский чат не совпадает с контекстом формы.
        """
        obj = super().get_object(request, object_id, from_field)
        if obj is not None and "room_id" in request.GET:
            if _child_room(obj).pk != _requested_room(request).pk:
                raise Http404("Объект относится к другому городскому чату.")
        return obj

    def has_view_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Проверяет права на просмотр родительского чата и дочерней модели.

        Args:
            request: Запрос администратора.
            obj: Проверяемый объект.

        Returns:
            Разрешён ли просмотр.
        """
        return _can_view_rooms(request) and super().has_view_permission(request, obj)

    def has_change_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Не позволяет изменять дочерний объект без доступа к его чату.

        Args:
            request: Запрос администратора.
            obj: Проверяемый объект.

        Returns:
            Разрешено ли изменение.
        """
        return _can_view_rooms(request) and super().has_change_permission(request, obj)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Проверяет доступ к чату и право добавления дочерней записи.

        Args:
            request: Запрос администратора.

        Returns:
            Разрешено ли добавление.
        """
        return _can_view_rooms(request) and super().has_add_permission(request)

    def has_delete_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Проверяет доступ к чату и право удаления дочерней записи.

        Args:
            request: Запрос администратора.
            obj: Проверяемый объект.

        Returns:
            Разрешено ли удаление.
        """
        return _can_view_rooms(request) and super().has_delete_permission(request, obj)

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, Any],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: Model | None = None,
    ) -> HttpResponse:
        """Добавляет возврат к родительскому чату и вложенные хлебные крошки.

        Args:
            request: Запрос администратора.
            context: Контекст формы.
            add: Создаётся ли запись.
            change: Открыта ли существующая запись.
            form_url: Адрес сохранения формы.
            obj: Дочерний объект либо None при добавлении.

        Returns:
            Форма в контексте выбранного города.
        """
        room = _child_room(obj) if obj is not None else _requested_room(request)
        context.update(
            chat_room=room,
            chat_room_url=_room_url(room),
            chat_parent_admin_url=reverse("admin:chat_chatroom_changelist"),
        )
        return super().render_change_form(request, context, add, change, form_url, obj)

    def response_post_save_add(
        self, request: HttpRequest, obj: Model
    ) -> HttpResponseRedirect:
        """Возвращает после сохранения к участникам выбранного города.

        Args:
            request: Запрос администратора.
            obj: Добавленный объект.

        Returns:
            Переход к блоку участников.
        """
        return HttpResponseRedirect(_room_url(_child_room(obj), members=True))

    def response_post_save_change(
        self, request: HttpRequest, obj: Model
    ) -> HttpResponseRedirect:
        """Сохраняет родительский контекст после редактирования.

        Args:
            request: Запрос администратора.
            obj: Изменённый объект.

        Returns:
            Переход к блоку участников.
        """
        return HttpResponseRedirect(_room_url(_child_room(obj), members=True))


@admin.register(ChatMembership)
class ChatMembershipAdmin(_RoomChildAdmin):
    """Добавляет, редактирует и удаляет участие внутри выбранного города.

    Attributes:
        readonly_fields: Служебные отметки, недоступные для ручного изменения.
    """

    autocomplete_fields = ("user",)
    readonly_fields = ("joined_at", "last_read_message_id")
    delete_confirmation_template = "admin/chat/member_delete_confirmation.html"

    def add_view(
        self,
        request: HttpRequest,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        """Предлагает сначала выбрать чат, если добавление открыли по старой ссылке.

        Args:
            request: Запрос администратора.
            form_url: Адрес сохранения формы.
            extra_context: Дополнительный контекст.

        Returns:
            Форма выбранного чата либо список городов.

        Raises:
            PermissionDenied: Нет права добавлять участников выбранного чата.
        """
        if not self.has_add_permission(request):
            raise PermissionDenied
        if "room_id" not in request.GET:
            return HttpResponseRedirect(reverse("admin:chat_chatroom_changelist"))
        return super().add_view(request, form_url, extra_context)

    def get_changeform_initial_data(
        self, request: HttpRequest
    ) -> dict[str, str | list[str]]:
        """Закрепляет выбранный чат в начальных значениях формы добавления.

        Args:
            request: Запрос администратора.

        Returns:
            Начальные значения с неизменяемым идентификатором комнаты.

        Raises:
            Http404: Родительский чат не найден.
        """
        return {
            **super().get_changeform_initial_data(request),
            "room": str(_requested_room(request).pk),
        }

    def get_form(
        self,
        request: HttpRequest,
        obj: Model | None = None,
        change: bool = False,
        **kwargs: Any,
    ) -> type[forms.ModelForm[Model]]:
        """Фиксирует город в форме, не позволяя переместить участника в другой чат.

        Args:
            request: Запрос администратора.
            obj: Изменяемое участие либо None.
            change: Открыта ли форма изменения.
            **kwargs: Параметры стандартной формы Django.

        Returns:
            Форма со всеми настройками участника и фиксированным городом.

        Raises:
            Http404: Город отсутствует или задан неверно.
        """
        form = super().get_form(request, obj, change=change, **kwargs)
        room = _child_room(obj) if obj is not None else _requested_room(request)
        if "room" in form.base_fields:
            form.base_fields["room"].disabled = True
            form.base_fields["room"].initial = room.pk
        return cast(type[forms.ModelForm[Model]], form)

    def save_model(
        self,
        request: HttpRequest,
        obj: Model,
        form: forms.ModelForm[Model],
        change: bool,
    ) -> None:
        """Сохраняет настройки и уведомляет открытые чаты об изменении роли.

        Args:
            request: Запрос администратора.
            obj: Участие пользователя.
            form: Проверенная форма.
            change: Изменяется ли существующая запись.
        """
        super().save_model(request, obj, form, change)
        room_id = _child_room(obj).pk
        transaction.on_commit(lambda: publish_room_change(room_id))

    def delete_view(
        self,
        request: HttpRequest,
        object_id: str,
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        """Удаляет участие с подтверждением, сохраняя аккаунт и историю сообщений.

        Args:
            request: Запрос администратора.
            object_id: Идентификатор участия.
            extra_context: Дополнительный контекст подтверждения.

        Returns:
            Подтверждение удаления либо возврат к участникам выбранного чата.
        """
        obj = self.get_object(request, object_id)
        room = _child_room(obj) if obj is not None else None
        context = {**(extra_context or {})}
        if room is not None:
            context.update(
                chat_room=room,
                chat_room_url=_room_url(room),
                chat_parent_admin_url=reverse("admin:chat_chatroom_changelist"),
            )
        response = super().delete_view(request, object_id, context)
        if (
            room is not None
            and request.method == "POST"
            and response.status_code == 302
        ):
            room_id = room.pk
            transaction.on_commit(lambda: publish_room_change(room_id))
            return HttpResponseRedirect(_room_url(room, members=True))
        return response


@admin.register(ChatMessage)
class ChatMessageAdmin(_RoomChildAdmin):
    """Показывает сообщение из карточки города без обхода сервисной модерации.

    Attributes:
        readonly_fields: Неизменяемые поля сообщения и журнала.
    """

    readonly_fields = (
        "room",
        "author",
        "text",
        "client_id",
        "reply_to",
        "revision",
        "created_at",
        "edited_at",
        "deleted_at",
    )
    actions = None

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запрещает создавать сообщения в обход сервисного слоя.

        Args:
            request: Запрос администратора.

        Returns:
            Всегда False.
        """
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Запрещает переписывать сообщение или его ревизию через админку.

        Args:
            request: Запрос администратора.
            obj: Проверяемое сообщение.

        Returns:
            Всегда False; просмотр регулируется отдельным разрешением.
        """
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Сохраняет историю вместо физического удаления сообщений.

        Args:
            request: Запрос администратора.
            obj: Проверяемое сообщение.

        Returns:
            Всегда False.
        """
        return False


@admin.register(ChatReport)
class ChatReportAdmin(admin.ModelAdmin):
    """Обрабатывает жалобы в отдельном разделе административной панели.

    Attributes:
        readonly_fields: Исходные данные жалобы, недоступные для изменения.
    """

    list_display = ("id", "message", "reporter", "is_resolved", "created_at")
    list_filter = ("is_resolved",)
    readonly_fields = ("message", "reporter", "reason", "created_at")
