"""Закрытый постраничный просмотр архивов и подтверждённое удаление владельцем."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Final

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Model, Q, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse

from .domain import MAX_DATABASE_ID
from .models import ChatArchive, ChatArchivedMessage, ChatMessageVersion

if TYPE_CHECKING:
    _ModelAdmin = admin.ModelAdmin[Any]
else:
    _ModelAdmin = admin.ModelAdmin

ARCHIVE_PAGE_SIZE: Final = 25


class ArchiveSearchForm(forms.Form):
    """Проверяет параметры поиска, не позволяя ошибочной дате вызвать сбой страницы.

    Attributes:
        q: Текст либо имя автора, включая предыдущие версии.
        author: Точный номер автора.
        start: Первый день периода отправки.
        end: Последний день периода отправки.
        status: Состояние сообщения.
    """

    q = forms.CharField(label="Текст или автор", required=False, max_length=100)
    author = forms.IntegerField(
        label="Номер автора", required=False, min_value=1, max_value=MAX_DATABASE_ID
    )
    start = forms.DateField(
        label="С даты", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    end = forms.DateField(
        label="По дату", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    status = forms.ChoiceField(
        label="Состояние",
        required=False,
        choices=[
            ("", "Все сообщения"),
            ("active", "Не удалены"),
            ("edited", "Редактировались"),
            ("deleted", "Удалены из чата"),
            ("incomplete", "Неполная ранняя история"),
        ],
    )

    def clean(self) -> dict[str, Any]:
        """Отклоняет период, в котором начальная дата позже конечной.

        Returns:
            Проверенные параметры фильтрации.
        """
        data = super().clean() or {}
        if data.get("start") and data.get("end") and data["start"] > data["end"]:
            self.add_error("end", "Конечная дата должна быть не раньше начальной.")
        return data


def _may_view(request: HttpRequest) -> bool:
    return bool(
        request.user.is_active
        and request.user.is_staff
        and request.user.has_perm("chat.view_chatarchive")
    )


def _filtered_messages(
    archive: ChatArchive, form: ArchiveSearchForm
) -> QuerySet[ChatArchivedMessage]:
    rows = archive.messages.all()
    if not form.is_valid():
        return rows.none()
    data = form.cleaned_data
    if data["q"]:
        rows = rows.filter(
            Q(text__icontains=data["q"])
            | Q(author_label__icontains=data["q"])
            | Q(versions__text__icontains=data["q"])
        ).distinct()
    if data["author"]:
        rows = rows.filter(author_reference=data["author"])
    if data["start"]:
        rows = rows.filter(sent_at__date__gte=data["start"])
    if data["end"]:
        rows = rows.filter(sent_at__date__lte=data["end"])
    if data["status"] == "active":
        rows = rows.filter(deleted_at__isnull=True)
    elif data["status"] == "deleted":
        rows = rows.filter(deleted_at__isnull=False)
    elif data["status"] == "edited":
        rows = rows.filter(edited_at__isnull=False)
    elif data["status"] == "incomplete":
        rows = rows.filter(history_incomplete=True)
    return rows


@admin.register(ChatArchive)
class ChatArchiveAdmin(_ModelAdmin):
    """Даёт сотрудникам просмотр архива, владельцу — отдельное удаление после закрытия чата.

    Attributes:
        change_form_template: Страница архива с фильтрами и пагинацией.
        readonly_fields: Метаданные, не редактируемые через админку.
    """

    list_display = (
        "city_label",
        "source_room_id",
        "room_created_at",
        "room_removed_at",
    )
    search_fields: ClassVar[tuple[str, ...]] = ("city_label", "source_room_id__exact")
    list_filter = ("room_removed_at",)
    list_per_page = ARCHIVE_PAGE_SIZE
    readonly_fields: ClassVar[tuple[str, ...]] = (
        "city_label",
        "source_room_id",
        "room_created_at",
        "room_removed_at",
        "created_at",
    )
    fields: ClassVar[tuple[str, ...]] = readonly_fields
    change_form_template = "admin/chat/archive_change_form.html"
    delete_confirmation_template = "admin/chat/archive_delete_confirmation.html"
    delete_selected_confirmation_template = (
        "admin/chat/archive_delete_selected_confirmation.html"
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запрещает ручное создание контейнеров вне жизненного цикла чата.

        Args:
            request: Запрос сотрудника.

        Returns:
            Всегда False.
        """
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Запрещает изменение сохранённой переписки.

        Args:
            request: Запрос сотрудника.
            obj: Архив.

        Returns:
            Всегда False.
        """
        return False

    def has_view_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Проверяет отдельное разрешение платформы на закрытые архивы.

        Args:
            request: Запрос сотрудника.
            obj: Архив.

        Returns:
            Наличие права чтения.
        """
        return _may_view(request)

    def has_delete_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Разрешает владельцу удалять только архивы уже удалённых чатов.

        Args:
            request: Запрос владельца платформы.
            obj: Архив либо проверка доступности действия в списке.

        Returns:
            Разрешено ли окончательное удаление.
        """
        return bool(
            request.user.is_active
            and request.user.is_staff
            and request.user.is_superuser
            and (obj is None or (isinstance(obj, ChatArchive) and obj.room_id is None))
        )

    def get_deleted_objects(
        self, objs: Sequence[Model] | QuerySet[Model], request: HttpRequest
    ) -> tuple[list[str], dict[str, int], set[str], list[str]]:
        """Показывает компактные счётчики каскада, не загружая все тексты и версии.

        Args:
            objs: Выбранные архивы.
            request: Запрос удаления.

        Returns:
            Дерево подтверждения с проверкой активных чатов.
        """
        archives: list[ChatArchive] = []
        missing: set[str] = set()
        for obj in objs:
            if not isinstance(obj, ChatArchive):
                raise PermissionDenied
            archives.append(obj)
            if not self.has_delete_permission(request, obj):
                missing.add(str(ChatArchive._meta.verbose_name))
        ids = [archive.pk for archive in archives]
        counts = {
            str(ChatArchive._meta.verbose_name_plural): len(archives),
            str(
                ChatArchivedMessage._meta.verbose_name_plural
            ): ChatArchivedMessage.objects.filter(archive_id__in=ids).count(),
            str(
                ChatMessageVersion._meta.verbose_name_plural
            ): ChatMessageVersion.objects.filter(message__archive_id__in=ids).count(),
        }
        # Здесь только собственные CASCADE-таблицы. Не строим дерево всех версий в памяти.
        return [str(archive) for archive in archives], counts, missing, []

    def delete_model(self, request: HttpRequest, obj: Model) -> None:
        """Использует единый проверяемый путь окончательного удаления.

        Args:
            request: Запрос владельца платформы.
            obj: Удаляемый архив.
        """
        self.delete_queryset(request, ChatArchive.objects.filter(pk=obj.pk))

    def response_delete(
        self, request: HttpRequest, obj_display: str, obj_id: int
    ) -> HttpResponse:
        """Возвращает владельца к архивам после окончательного удаления.

        Args:
            request: Запрос владельца платформы.
            obj_display: Название удалённого архива.
            obj_id: Идентификатор удалённого объекта.

        Returns:
            Переход к списку архивов с сообщением об удалении.
        """
        response = super().response_delete(request, obj_display, obj_id)
        if _may_view(request):
            return HttpResponseRedirect(reverse("admin:chat_chatarchive_changelist"))
        return response

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Model]) -> None:
        """Атомарно удаляет только архивы без живых комнат, включая все версии.

        Args:
            request: Запрос владельца платформы.
            queryset: Выбранные архивы.

        Raises:
            PermissionDenied: Не владелец платформы либо выбран активный чат.
        """
        if not self.has_delete_permission(request):
            raise PermissionDenied
        with transaction.atomic():
            ids = list(queryset.select_for_update().values_list("pk", flat=True))
            rows = ChatArchive.objects.filter(pk__in=ids)
            if rows.filter(room__isnull=False).exists():
                raise PermissionDenied(
                    "Сначала удалите действующий чат. Его архив пока защищён."
                )
            rows.delete()

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, Any],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: Model | None = None,
    ) -> HttpResponse:
        """Загружает только страницу найденных сообщений, а не всю переписку.

        Args:
            request: Запрос с поиском и фильтрами.
            context: Стандартный контекст Django.
            add: Создание объекта.
            change: Изменение объекта.
            form_url: Адрес формы.
            obj: Выбранный архив.

        Returns:
            Закрытая страница архива.
        """
        if isinstance(obj, ChatArchive):
            form = ArchiveSearchForm(request.GET)
            page = Paginator(_filtered_messages(obj, form), ARCHIVE_PAGE_SIZE).get_page(
                request.GET.get("page")
            )
            query = request.GET.copy()
            query.pop("page", None)
            context.update(
                archive_search_form=form,
                archive_messages_page=page,
                archive_query=query.urlencode(),
                archive_counts=obj.messages.aggregate(
                    total=Count("pk"),
                    deleted=Count("pk", filter=Q(deleted_at__isnull=False)),
                ),
            )
        return super().render_change_form(request, context, add, change, form_url, obj)


@admin.register(ChatArchivedMessage)
class ChatArchivedMessageAdmin(ChatArchiveAdmin):
    """Показывает версии одного сообщения только внутри закрытого архива.

    Attributes:
        readonly_fields: Неизменяемые снимки автора, текста и времени.
        change_form_template: Шаблон постраничной истории версий.
    """

    list_display = ("source_message_id", "author_label", "sent_at", "deleted_at")
    search_fields = ("author_label", "text", "author_reference__exact")
    list_filter = ("deleted_at",)
    readonly_fields: ClassVar[tuple[str, ...]] = (
        "source_message_id",
        "author_reference",
        "author_label",
        "sent_at",
        "edited_at",
        "deleted_at",
        "text",
        "history_incomplete",
    )
    fields: ClassVar[tuple[str, ...]] = readonly_fields
    change_form_template = "admin/chat/archive_message_change_form.html"
    actions = None

    def get_model_perms(self, request: HttpRequest) -> dict[str, bool]:
        """Скрывает дочерние записи из основного меню.

        Args:
            request: Запрос меню.

        Returns:
            Пустое отображение прав для навигации.
        """
        return {}

    def has_delete_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        """Запрещает выборочное уничтожение отдельных доказательных версий.

        Args:
            request: Запрос сотрудника.
            obj: Снимок сообщения.

        Returns:
            Всегда False.
        """
        return False

    def render_change_form(
        self,
        request: HttpRequest,
        context: dict[str, Any],
        add: bool = False,
        change: bool = False,
        form_url: str = "",
        obj: Model | None = None,
    ) -> HttpResponse:
        """Передаёт только одну страницу версий и ссылку на исходный архив.

        Args:
            request: Запрос сотрудника.
            context: Стандартный контекст Django.
            add: Создание объекта.
            change: Изменение объекта.
            form_url: Адрес формы.
            obj: Архивный снимок.

        Returns:
            История выбранного сообщения.
        """
        if isinstance(obj, ChatArchivedMessage):
            context.update(
                private_archive=obj.archive,
                archive_versions_page=Paginator(
                    obj.versions.all(), ARCHIVE_PAGE_SIZE
                ).get_page(request.GET.get("page")),
            )
        return super().render_change_form(request, context, add, change, form_url, obj)
