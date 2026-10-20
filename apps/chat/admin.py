"""Управление городским чатом, его участниками и перепиской из одной карточки."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING, Any, Final, cast

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, Model, OuterRef, Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse

from apps.core.models import LegalAcceptanceLog
from apps.users.models import Notification, User

from . import archive_admin as archive_admin
from . import invitation_admin as invitation_admin
from .consent import (
    MAX_USER_AGENT_LENGTH,
    consent_request_ip,
    ensure_chat_consent,
    publish_disclosure_change,
)
from .domain import MAX_DATABASE_ID, ChatError
from .models import (
    ChatArchive,
    ChatInvitationCampaign,
    ChatManagementEvent,
    ChatMembership,
    ChatMessage,
    ChatReport,
    ChatRoom,
)
from .realtime import publish_room_change

# Django не поддерживает параметризацию ModelAdmin во время исполнения.
if TYPE_CHECKING:
    _ModelAdmin = admin.ModelAdmin[Any]
else:
    _ModelAdmin = admin.ModelAdmin

ADMIN_PAGE_SIZE: Final = 25


def _can_view_rooms(request: HttpRequest) -> bool:
    return bool(
        request.user.has_perm("chat.view_chatroom")
        or request.user.has_perm("chat.change_chatroom"),
    )


def _can_view(request: HttpRequest, model: str) -> bool:
    return bool(
        request.user.has_perm(f"chat.view_{model}")
        or request.user.has_perm(f"chat.change_{model}"),
    )


def _room_url(room: ChatRoom, *, members: bool = False) -> str:
    url = reverse("admin:chat_chatroom_change", args=[room.pk])
    return url + ("#chat-room-members" if members else "")


def _child_room(obj: Model) -> ChatRoom:
    if not isinstance(obj, (ChatMembership, ChatMessage)):
        raise Http404("Объект не относится к городскому чату.")
    return obj.room


def _requested_room(request: HttpRequest) -> ChatRoom:
    try:
        room_id = int(request.GET.get("room_id", ""))
    except ValueError as exc:
        raise Http404("Выберите городской чат.") from exc
    if not 0 < room_id <= MAX_DATABASE_ID:
        raise Http404("Городской чат не найден.")
    return get_object_or_404(ChatRoom.objects.select_related("city"), pk=room_id)


if TYPE_CHECKING:
    _RoomForm = forms.ModelForm[ChatRoom]
else:
    _RoomForm = forms.ModelForm


class ChatRoomForm(_RoomForm):
    """Проверяет назначение администратора платформой на действующего участника.

    Attributes:
        Meta: Модель комнаты и её редактируемые поля.
    """

    class Meta:
        model = ChatRoom
        fields = "__all__"

    def clean_administrator(self) -> User | None:
        """Допускает только активного участника с действующими согласиями.

        Returns:
            Проверенный администратор либо пустое назначение.

        Raises:
            forms.ValidationError: Выбран посторонний, заблокированный участник или нет согласий.
        """
        user = cast(User | None, self.cleaned_data.get("administrator"))
        if user is None:
            return None
        member = ChatMembership.objects.filter(
            room_id=self.instance.pk, user=user, is_active=True, user__is_active=True
        ).first()
        if member is None:
            raise forms.ValidationError(
                "Сначала участник должен вступить в этот чат и подтвердить согласия."
            )
        try:
            ensure_chat_consent(member)
        except ChatError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return user


class AdministratorFilter(admin.SimpleListFilter):
    """Выделяет сообщества без действующего администратора для команды платформы.

    Attributes:
        title: Название фильтра.
        parameter_name: Параметр адреса списка.
    """

    title = "Администратор чата"
    parameter_name = "administrator_state"

    def lookups(
        self, request: HttpRequest, model_admin: admin.ModelAdmin[Any]
    ) -> list[tuple[str, str]]:
        """Возвращает варианты наличия управления.

        Args:
            request: Запрос списка.
            model_admin: Администратор модели.

        Returns:
            Варианты фильтра.
        """
        return [("missing", "Без администратора"), ("assigned", "С администратором")]

    def queryset(
        self, request: HttpRequest, queryset: QuerySet[ChatRoom]
    ) -> QuerySet[ChatRoom]:
        """Находит также комнаты заблокированных или исключённых управляющих.

        Args:
            request: Запрос списка.
            queryset: Исходные комнаты.

        Returns:
            Комнаты согласно выбранному состоянию.
        """
        valid = ChatMembership.objects.filter(
            room_id=OuterRef("pk"),
            user_id=OuterRef("administrator_id"),
            is_active=True,
            user__is_active=True,
            consented_at__isnull=False,
            consent_withdrawn_at__isnull=True,
        ).filter(Q(disclosure_conditions="") | Q(disclosure_reviewed=True))
        if self.value() in {"missing", "assigned"}:
            return queryset.annotate(has_administrator=Exists(valid)).filter(
                is_created=True, has_administrator=self.value() == "assigned"
            )
        return queryset


@admin.register(ChatRoom)
class ChatRoomAdmin(_ModelAdmin):
    """Объединяет настройки комнаты, сообщения и управление её участниками.

    Attributes:
        change_form_template: Шаблон карточки с двумя вложенными блоками.
    """

    form = ChatRoomForm
    readonly_fields = ("is_created", "direct_user_low", "direct_user_high")
    list_display = (
        "display_label",
        "city",
        "is_created",
        "is_active",
        "administrator",
        "created_at",
    )
    list_filter = ("is_active", "is_created", AdministratorFilter)
    search_fields = ("title", "city__name", "city__region")
    autocomplete_fields = ("city", "administrator")
    change_form_template = "admin/chat/room_change_form.html"
    delete_confirmation_template = "admin/chat/room_delete_confirmation.html"
    delete_selected_confirmation_template = (
        "admin/chat/room_delete_selected_confirmation.html"
    )

    def get_readonly_fields(
        self, request: HttpRequest, obj: ChatRoom | None = None
    ) -> tuple[str, ...]:
        """Разрешает создавать и именовать отдельные комнаты только владельцу.

        Args:
            request: Запрос сотрудника платформы.
            obj: Редактируемый чат либо новая запись.

        Returns:
            Поля, недоступные для редактирования текущему сотруднику.
        """
        if obj and obj.is_direct:
            return (*self.readonly_fields, "title", "city", "administrator")
        if not request.user.is_superuser:
            return (*self.readonly_fields, "title")
        return self.readonly_fields

    def get_deleted_objects(
        self, objs: Sequence[Model] | QuerySet[Model], request: HttpRequest
    ) -> tuple[list[str], dict[str, int], set[str], list[str]]:
        """Разрешает каскад сообщений только при удалении самой комнаты.

        Args:
            objs: Выбранные городские чаты.
            request: Запрос удаления владельца либо уполномоченного сотрудника.

        Returns:
            Стандартное подтверждение Django с сохранением остальных проверок прав и защит.
        """
        deleted, counts, missing, protected = super().get_deleted_objects(objs, request)
        if self.has_delete_permission(request) and request.user.has_perm(
            "chat.delete_chatinvitationcampaign"
        ):
            missing.discard(str(ChatInvitationCampaign._meta.verbose_name))
        if self.has_delete_permission(request) and request.user.has_perm(
            "chat.delete_chatmessage"
        ):
            missing.discard(str(ChatMessage._meta.verbose_name))
        return deleted, counts, missing, protected

    def delete_model(self, request: HttpRequest, obj: Model) -> None:
        """Удаляет один чат тем же атомарным путём, что и массовая операция.

        Args:
            request: Подтверждённый запрос удаления.
            obj: Городской чат.
        """
        self.delete_queryset(request, ChatRoom.objects.filter(pk=obj.pk))

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Model]) -> None:
        """Удаляет комнаты, зависимые записи и уведомления только выбранных чатов.

        Args:
            request: Уполномоченный сотрудник платформы.
            queryset: Подтверждённый набор городских чатов.
        """
        with transaction.atomic():
            room_ids = list(
                queryset.select_for_update(of=("self",)).values_list("pk", flat=True)
            )
            Notification.objects.filter(
                url__in=[reverse("chat:room", args=[pk]) for pk in room_ids]
            ).delete()
            queryset.filter(pk__in=room_ids).delete()
            for room_id in room_ids:
                transaction.on_commit(partial(publish_room_change, room_id))

    def get_queryset(self, request: HttpRequest) -> QuerySet[ChatRoom]:
        """Загружает города вместе с комнатами без дополнительных запросов.

        Args:
            request: Запрос администратора.

        Returns:
            Комнаты с населёнными пунктами.
        """
        return super().get_queryset(request).select_related("city", "administrator")

    def save_model(
        self,
        request: HttpRequest,
        obj: Model,
        form: forms.ModelForm[Model],
        change: bool,
    ) -> None:
        """Фиксирует назначение администратора платформой в журнале комнаты.

        Args:
            request: Сотрудник с разрешением изменять комнаты.
            obj: Комната с проверенной формой.
            form: Данные Django-формы.
            change: Изменяется ли существующая комната.
        """
        assert isinstance(obj, ChatRoom)
        with transaction.atomic():
            old = (
                ChatRoom.objects.select_for_update().get(pk=obj.pk) if change else None
            )
            if not change and obj.city_id is None:
                if not request.user.is_superuser:
                    raise PermissionDenied(
                        "Создавать отдельные чаты может только владелец платформы."
                    )
                obj.administrator = request.user
            super().save_model(request, obj, form, change)
            if not change and obj.city_id is None:
                ChatManagementEvent.objects.create(
                    room=obj,
                    actor=cast(User, request.user),
                    target=obj.administrator,
                    action="created",
                )
            if old and old.administrator_id != obj.administrator_id:
                ChatManagementEvent.objects.create(
                    room=obj,
                    actor=cast(User, request.user),
                    target=obj.administrator,
                    action="assigned" if obj.administrator_id else "released",
                )
                publish_disclosure_change(
                    room_id=obj.pk,
                    user_id=obj.administrator_id or old.administrator_id or 0,
                )

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
            chat_management_events=(),
            chat_invitation_campaigns=(),
            chat_archive=None,
            chat_archive_counts=None,
            chat_members_page=None,
            chat_can_add_member=request.user.has_perm("chat.add_chatmembership"),
            chat_can_change_member=request.user.has_perm("chat.change_chatmembership"),
            chat_can_delete_member=request.user.has_perm("chat.delete_chatmembership"),
        )
        if obj is not None:
            if request.user.has_perm("chat.view_chatinvitationcampaign"):
                context["chat_invitation_campaigns"] = (
                    obj.invitation_campaigns.select_related("sender")[:ADMIN_PAGE_SIZE]
                )
            if request.user.has_perm("chat.change_chatroom"):
                context["chat_management_events"] = (
                    obj.management_events.select_related("actor", "target")[
                        :ADMIN_PAGE_SIZE
                    ]
                )
            if request.user.has_perm("chat.view_chatarchive"):
                archive = ChatArchive.objects.filter(source_room_id=obj.pk).first()
                if archive:
                    context["chat_archive"] = archive
                    context["chat_archive_counts"] = archive.messages.aggregate(
                        total=Count("pk"),
                        deleted=Count("pk", filter=Q(deleted_at__isnull=False)),
                    )
            if _can_view(request, "chatmembership"):
                context["chat_members_page"] = Paginator(
                    obj.memberships.select_related("user").order_by(
                        "-is_active", "user__last_name", "user__first_name", "pk"
                    ),
                    ADMIN_PAGE_SIZE,
                ).get_page(request.GET.get("members_page"))
        return super().render_change_form(request, context, add, change, form_url, obj)


class _RoomChildAdmin(_ModelAdmin):
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
    readonly_fields = (
        "joined_at",
        "last_read_message_id",
        "consent_version",
        "consent_id",
        "consented_at",
        "consent_withdrawn_at",
        "disclosure_name",
        "disclosure_avatar",
        "disclosure_conditions",
    )
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
        if obj is not None and "user" in form.base_fields:
            form.base_fields["user"].disabled = True
        if "disclosure_reviewed" in form.base_fields and not request.user.is_superuser:
            form.base_fields["disclosure_reviewed"].disabled = True
        return cast("type[forms.ModelForm[Model]]", form)

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
        room_id = _child_room(obj).pk
        role_changed = False
        review_changed = (
            isinstance(obj, ChatMembership)
            and "disclosure_reviewed" in form.changed_data
        )
        if change and isinstance(obj, ChatMembership):
            ChatRoom.objects.select_for_update().get(pk=room_id)
            current = ChatMembership.objects.select_for_update().get(pk=obj.pk)
            role_changed = current.is_moderator != obj.is_moderator
            fields = list(form.changed_data)
            if review_changed and current.consent_id != obj.consent_id:
                fields.remove("disclosure_reviewed")
                review_changed = False
                self.message_user(
                    request,
                    "Игрок изменил согласие. Особые условия необходимо проверить заново.",
                    level="warning",
                )
            # Не сохраняем старую копию readonly-полей поверх нового согласия игрока.
            if fields:
                obj.save(update_fields=fields)
            obj.refresh_from_db()
        else:
            super().save_model(request, obj, form, change)
            role_changed = isinstance(obj, ChatMembership) and obj.is_moderator
        if isinstance(obj, ChatMembership) and role_changed:
            ChatManagementEvent.objects.create(
                room_id=room_id,
                actor=cast(User, request.user),
                target=obj.user,
                action="moderator_added" if obj.is_moderator else "moderator_removed",
            )
        if isinstance(obj, ChatMembership) and review_changed:
            if obj.consented_at:
                LegalAcceptanceLog.objects.create(
                    user_id=obj.user_id,
                    document_slug="chat-disclosure",
                    document_version=obj.consent_version or "unknown",
                    source="chat_conditions_review",
                    ip_address=consent_request_ip(request),
                    user_agent=request.META.get("HTTP_USER_AGENT", "")[
                        :MAX_USER_AGENT_LENGTH
                    ],
                    metadata={
                        "room_id": room_id,
                        "city_id": obj.room.city_id,
                        "acceptance_id": str(obj.consent_id),
                        "operator_user_id": request.user.pk,
                        "conditions": obj.disclosure_conditions,
                        "reviewed": obj.disclosure_reviewed,
                        "event": "operator_conditions_review_not_player_consent",
                    },
                )
            publish_disclosure_change(room_id=room_id, user_id=obj.user_id)
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
class ChatReportAdmin(_ModelAdmin):
    """Обрабатывает жалобы в отдельном разделе административной панели.

    Attributes:
        readonly_fields: Исходные данные жалобы, недоступные для изменения.
    """

    list_display = ("id", "message", "reporter", "is_resolved", "created_at")
    list_filter = ("is_resolved",)
    readonly_fields = ("message", "reporter", "reason", "created_at")
