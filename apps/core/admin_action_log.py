"""Админка полного журнала действий в Django admin."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.contrib import admin
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.db.models import Q, QuerySet
from django.http import HttpRequest
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from apps.core.admin_logging import format_log_entry
from apps.core.models import AdminActionLog

ACTION_FLAG_LABELS: dict[int, str] = {
    ADDITION: "Добавлено",
    CHANGE: "Изменено",
    DELETION: "Удалено",
}

try:
    admin.site.unregister(LogEntry)
except admin.sites.NotRegistered:
    pass


@admin.register(AdminActionLog)
class AdminActionLogAdmin(admin.ModelAdmin):
    """Полный журнал действий администраторов с поиском."""

    change_list_template = "admin/core/adminactionlog/change_list.html"
    list_display = (
        "action_time",
        "admin_user_display",
        "action_label",
        "object_display",
        "content_type_display",
        "change_details_display",
    )
    list_filter = ("action_flag", "user", "content_type")
    date_hierarchy = "action_time"
    list_per_page = 50
    ordering = ("-action_time", "-pk")
    search_fields = ()
    show_full_result_count = True

    def has_module_permission(self, request: HttpRequest) -> bool:
        """Разрешить доступ к модулю только staff-пользователям."""
        return bool(request.user.is_staff)

    def has_view_permission(
        self,
        request: HttpRequest,
        obj: AdminActionLog | None = None,
    ) -> bool:
        """Разрешить просмотр журнала staff-пользователям."""
        return bool(request.user.is_staff)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запретить ручное создание записей журнала."""
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: AdminActionLog | None = None,
    ) -> bool:
        """Запретить редактирование записей журнала."""
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: AdminActionLog | None = None,
    ) -> bool:
        """Удаление записей журнала доступно только суперпользователю."""
        return bool(request.user.is_superuser)

    def get_queryset(self, request: HttpRequest) -> QuerySet[AdminActionLog]:
        """Вернуть queryset с предзагрузкой связей и пользовательскими фильтрами."""
        qs = super().get_queryset(request).select_related("user", "content_type")
        return self._apply_search_filters(request, qs)

    def _apply_search_filters(
        self,
        request: HttpRequest,
        qs: QuerySet[AdminActionLog],
    ) -> QuerySet[AdminActionLog]:
        """Применить поиск по ФИО, дате или типу действия.

        Args:
            request: HTTP-запрос changelist.
            qs: Базовый queryset.

        Returns:
            Отфильтрованный queryset.
        """
        mode = request.GET.get("log_search_mode", "").strip()
        query = request.GET.get("log_query", "").strip()
        search_date = request.GET.get("log_search_date", "").strip()
        action_flag = request.GET.get("log_action_flag", "").strip()

        if mode == "fio" and query:
            return qs.filter(
                Q(object_repr__icontains=query)
                | Q(user__first_name__icontains=query)
                | Q(user__last_name__icontains=query)
                | Q(user__email__icontains=query)
                | Q(change_message__icontains=query)
            )
        if mode == "date" and search_date:
            try:
                day = datetime.strptime(search_date, "%Y-%m-%d").date()
            except ValueError:
                return qs
            return qs.filter(action_time__date=day)
        if mode == "action":
            if action_flag in {"1", "2", "3"}:
                return qs.filter(action_flag=int(action_flag))
            if query:
                query_lower = query.lower()
                for flag, label in ACTION_FLAG_LABELS.items():
                    if label.lower() == query_lower or label.lower().startswith(
                        query_lower
                    ):
                        return qs.filter(action_flag=flag)
                return qs.filter(change_message__icontains=query)
        return qs

    def changelist_view(
        self,
        request: HttpRequest,
        extra_context: dict[str, Any] | None = None,
    ) -> Any:
        """Добавить параметры поиска и общую статистику в контекст changelist."""
        context = extra_context or {}
        context.update(
            {
                "search_mode": request.GET.get("log_search_mode", "fio"),
                "search_q": request.GET.get("log_query", ""),
                "search_date": request.GET.get("log_search_date", ""),
                "action_flag_filter": request.GET.get("log_action_flag", ""),
                "action_flag_choices": ACTION_FLAG_LABELS,
                "total_log_count": LogEntry.objects.count(),
            }
        )
        return super().changelist_view(request, context)

    @admin.display(description="Администратор", ordering="user")
    def admin_user_display(self, obj: AdminActionLog) -> str:
        """Отобразить администратора, выполнившего действие."""
        if not obj.user_id:
            return "—"
        user = obj.user
        full_name = user.get_full_name().strip()
        if full_name:
            return str(full_name)
        return str(user.email or user.pk)

    @admin.display(description="Действие", ordering="action_flag")
    def action_label(self, obj: AdminActionLog) -> str:
        """Отобразить тип действия на русском."""
        return str(ACTION_FLAG_LABELS.get(obj.action_flag, obj.action_flag))

    @admin.display(description="Объект", ordering="object_repr")
    def object_display(self, obj: AdminActionLog) -> SafeString | str:
        """Отобразить объект с ссылкой на карточку в админке."""
        if obj.is_deletion or not obj.get_admin_url():
            return str(obj.object_repr)
        return format_html('<a href="{}">{}</a>', obj.get_admin_url(), obj.object_repr)

    @admin.display(description="Тип объекта", ordering="content_type")
    def content_type_display(self, obj: AdminActionLog) -> str:
        """Отобразить тип изменённого объекта."""
        if not obj.content_type_id:
            return "—"
        return str(obj.content_type.name).capitalize()

    @admin.display(description="Детали")
    def change_details_display(self, obj: AdminActionLog) -> SafeString | str:
        """Отобразить детали изменений."""
        lines = format_log_entry(obj)
        if not lines:
            return "—"
        return format_html_join("<br>", "{}", ((line,) for line in lines))
