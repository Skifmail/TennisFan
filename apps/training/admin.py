"""Настройка админки тренеров, тренировок и распределения заявок."""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import URLPattern, path
from django.utils.decorators import method_decorator
from django.utils.html import format_html
from django.views.decorators.http import require_http_methods

from apps.training.coach_defaults import coach_defaults_from_user
from apps.users.models import User

from .enrollment_services import (
    available_coaches,
    distribute_enrollment,
    is_enrollment_admin,
)
from .forms import AdminTrainingForm, CoachAdminForm, CoachApplicationAdminForm
from .models import (
    Coach,
    CoachApplication,
    CoachApplicationStatus,
    Training,
    TrainingEnrollment,
)

logger = logging.getLogger(__name__)


@admin.register(Coach)
class CoachAdmin(admin.ModelAdmin):
    """Admin for Coach model."""

    form = CoachAdminForm

    class Media:
        js = ("js/city_autocomplete.js", "js/admin_coach_user_defaults.js")

    list_display = (
        "name",
        "user",
        "city",
        "sport",
        "experience_years",
        "specialization",
        "is_active",
    )
    list_filter = ("sport", "city", "is_active")
    search_fields = ("name", "bio", "specialization")
    list_editable = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}

    def get_urls(self):
        custom_urls = [
            path(
                "user-defaults/<int:user_id>/",
                self.admin_site.admin_view(self.user_defaults_view),
                name="training_coach_user_defaults",
            ),
        ]
        return custom_urls + super().get_urls()

    def user_defaults_view(self, request: HttpRequest, user_id: int) -> JsonResponse:
        """Вернуть известные поля профиля для автозаполнения формы тренера.

        Args:
            request: Запрос сотрудника админки.
            user_id: Идентификатор выбранного пользователя.

        Returns:
            JsonResponse: Имя, контакты и город из профиля игрока.
        """
        user = get_object_or_404(
            User.objects.select_related("player"),
            pk=user_id,
        )
        return JsonResponse(coach_defaults_from_user(user))


@admin.action(description="Одобрить и добавить тренера на сайт")
def approve_coach_applications(modeladmin, request, queryset):
    pending = queryset.filter(status=CoachApplicationStatus.PENDING)
    ok = err = 0
    for app in pending:
        try:
            app.approve_and_create_coach()
            ok += 1
        except Exception as e:
            logger.exception("Ошибка одобрения заявки тренера %s: %s", app.pk, e)
            err += 1
    if ok:
        messages.success(
            request, f"Одобрено заявок: {ok}. Тренеры добавлены в «Наши тренеры»."
        )
    if err:
        messages.error(request, f"Не удалось одобрить заявок: {err}. См. лог.")


@admin.action(description="Отклонить заявки")
def reject_coach_applications(modeladmin, request, queryset):
    pending = list(queryset.filter(status=CoachApplicationStatus.PENDING))
    updated = 0
    for app in pending:
        app.status = CoachApplicationStatus.REJECTED
        app.save(update_fields=["status", "updated_at"])
        updated += 1
        try:
            from apps.core.email_service import send_coach_application_decision_email

            send_coach_application_decision_email(app, approved=False)
        except Exception:
            logger.exception(
                "send_coach_application_decision_email failed | application=%s",
                app.pk,
            )
    if updated:
        messages.success(request, f"Отклонено заявок: {updated}.")


@admin.register(CoachApplication)
class CoachApplicationAdmin(admin.ModelAdmin):
    """Заявки «Стать тренером». После одобрения создаётся Coach."""

    form = CoachApplicationAdminForm

    class Media:
        js = ("js/city_autocomplete.js",)

    list_display = (
        "name",
        "city",
        "sport",
        "applicant_user",
        "applicant_name",
        "applicant_email",
        "status_badge",
        "coach_link",
        "created_at",
    )
    list_filter = ("status", "sport", "city")
    search_fields = (
        "name",
        "city",
        "applicant_name",
        "applicant_email",
        "specialization",
    )
    list_display_links = ("name",)
    actions = [approve_coach_applications, reject_coach_applications]
    readonly_fields = ("status", "coach", "created_at", "updated_at")

    fieldsets = (
        (
            "Заявитель",
            {"fields": ("applicant_name", "applicant_email", "applicant_phone")},
        ),
        (
            None,
            {
                "fields": (
                    "name",
                    "photo",
                    "bio",
                    "experience_years",
                    "specialization",
                    "city",
                    "sports",
                )
            },
        ),
        ("Контакты", {"fields": ("phone", "telegram", "whatsapp", "max_contact")}),
        ("Статус", {"fields": ("status", "coach", "created_at", "updated_at")}),
    )

    def status_badge(self, obj):
        colors = {
            CoachApplicationStatus.PENDING: "#f0ad4e",
            CoachApplicationStatus.APPROVED: "#5cb85c",
            CoachApplicationStatus.REJECTED: "#d9534f",
        }
        c = colors.get(obj.status, "#999")
        return format_html(
            '<span style="background: {}; color: #fff; padding: 2px 8px; border-radius: 4px;">{}</span>',
            c,
            obj.get_status_display(),
        )

    status_badge.short_description = "Статус"

    def coach_link(self, obj):
        if not obj.coach_id:
            return "—"
        return format_html(
            '<a href="{}">{}</a>',
            f"/admin/training/coach/{obj.coach_id}/change/",
            obj.coach.name,
        )

    coach_link.short_description = "Тренер на сайте"


@admin.register(Training)
class TrainingAdmin(admin.ModelAdmin):
    """Admin for Training model."""

    class Media:
        js = ("js/city_autocomplete.js",)

    form = AdminTrainingForm
    list_display = (
        "title",
        "sport",
        "types_display",
        "levels_display",
        "coach",
        "courts_display",
        "city",
        "price_range_display",
        "court_price_range_display",
        "is_active",
    )
    list_filter = ("sport", "city", "is_active")
    search_fields = ("title", "description")
    list_editable = ("is_active",)
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("coach",)
    filter_horizontal = ("courts",)

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "title",
                    "slug",
                    "sports",
                    "short_description",
                    "description",
                    "image",
                )
            },
        ),
        (
            "Типы тренировки и цены",
            {"fields": ("type_prices",)},
        ),
        (
            "Уровни",
            {"fields": ("skill_levels", "target_levels")},
        ),
        ("Тренер и место", {"fields": ("coach", "courts", "city")}),
        ("Параметры", {"fields": ("duration_minutes", "max_participants", "schedule")}),
        (
            "Стоимость корта",
            {"fields": ("court_has_extra_fee", "court_price_min", "court_price_max")},
        ),
        ("Настройки", {"fields": ("is_active",)}),
    )

    def types_display(self, obj):
        return ", ".join(obj.training_types_display) or "—"

    types_display.short_description = "Типы"

    def levels_display(self, obj):
        return ", ".join(obj.skill_levels_display) or "—"

    levels_display.short_description = "Уровни"

    def courts_display(self, obj):
        names = list(obj.courts.values_list("name", flat=True)[:6])
        if not names:
            return "—"
        return ", ".join(names[:5]) + (" …" if len(names) > 5 else "")

    courts_display.short_description = "Корты"

    def price_range_display(self, obj):
        return obj.price_display or "—"

    price_range_display.short_description = "Цена"

    def court_price_range_display(self, obj):
        return obj.court_price_display or "—"

    court_price_range_display.short_description = "Корт"


@admin.register(TrainingEnrollment)
class TrainingEnrollmentAdmin(admin.ModelAdmin):
    """Все записи на тренировки и их распределение администраторами платформы."""

    change_list_template = "admin/training/trainingenrollment/change_list.html"
    change_form_template = "admin/training/trainingenrollment/change_form.html"

    class Media:
        css = {"all": ("admin/css/training_enrollment.css",)}

    list_display = (
        "applicant_summary",
        "training_summary",
        "coach_summary",
        "status",
        "distribution_actions",
    )
    list_display_links = None
    list_filter = (
        "assigned_at",
        "assigned_coach",
        "requested_coach",
        "status",
        "training",
    )
    list_select_related = (
        "training",
        "player__user",
        "desired_court",
        "assigned_coach",
    )
    search_fields = (
        "full_name",
        "email",
        "telegram",
        "whatsapp",
        "requested_coach_name",
        "player__user__first_name",
        "player__user__last_name",
    )
    list_editable = ("status",)
    raw_id_fields = ("training", "player")
    date_hierarchy = "created_at"
    readonly_fields = (
        "requested_coach",
        "requested_coach_name",
        "assigned_coach",
        "assigned_by",
        "assigned_at",
        "distribution_actions",
        "created_at",
        "updated_at",
    )

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        """Дать списку короткий заголовок, не меняя поиск, фильтры и формы Django."""
        return super().changelist_view(
            request, {"title": "Заявки на тренировки", **(extra_context or {})}
        )

    @admin.display(description="Заявка / контакт", ordering="created_at")
    def applicant_summary(self, obj: TrainingEnrollment) -> str:
        """Объединить имя, контакт, номер и дату заявки без дублирования профиля."""
        return str(
            render_to_string(
                "admin/training/trainingenrollment/applicant_summary.html",
                {"enrollment": obj},
            )
        )

    @admin.display(description="Тренировка", ordering="training__title")
    def training_summary(self, obj: TrainingEnrollment) -> str:
        """Показать тренировку и место в одной компактной ячейке."""
        return str(
            render_to_string(
                "admin/training/trainingenrollment/training_summary.html",
                {"enrollment": obj},
            )
        )

    @admin.display(description="Тренер / распределение", ordering="assigned_at")
    def coach_summary(self, obj: TrainingEnrollment) -> str:
        """Различить исходного тренера и получателя без отдельных колонок."""
        return str(
            render_to_string(
                "admin/training/trainingenrollment/coach_summary.html",
                {"enrollment": obj},
            )
        )

    def formfield_for_choice_field(
        self, db_field: models.Field[Any, Any], request: HttpRequest, **kwargs: Any
    ) -> Any:
        """Подписать выпадающий статус для чтения без заголовка таблицы на телефоне."""
        field = super().formfield_for_choice_field(db_field, request, **kwargs)
        if db_field.name == "status" and field is not None:
            field.widget.attrs["aria-label"] = "Статус заявки"
        return field

    def has_module_permission(self, request: HttpRequest) -> bool:
        """Скрыть раздел от сотрудников без полномочий распределения."""
        return is_enrollment_admin(request.user)

    def has_view_permission(
        self, request: HttpRequest, obj: TrainingEnrollment | None = None
    ) -> bool:
        """Разрешить чтение всех заявок только администраторам распределения."""
        return is_enrollment_admin(request.user)

    def has_change_permission(
        self, request: HttpRequest, obj: TrainingEnrollment | None = None
    ) -> bool:
        """Проверить права изменения заявок и выполнения кнопок распределения."""
        return is_enrollment_admin(request.user)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Проверить права ручного создания заявки."""
        return is_enrollment_admin(request.user) and super().has_add_permission(request)

    def has_delete_permission(
        self, request: HttpRequest, obj: TrainingEnrollment | None = None
    ) -> bool:
        """Не разрешать удаление чужих записей обычному тренеру-сотруднику."""
        return is_enrollment_admin(request.user) and super().has_delete_permission(
            request, obj
        )

    def get_queryset(self, request: HttpRequest) -> QuerySet[TrainingEnrollment]:
        """Загрузить все записи без дополнительных запросов к назначениям."""
        return (
            super()
            .get_queryset(request)
            .select_related(
                "training",
                "assigned_coach",
                "assigned_by",
                "player__user",
                "desired_court",
            )
        )

    @admin.display(description="Распределение")
    def distribution_state(self, obj: TrainingEnrollment) -> str:
        """Показать независимый от обработки заявки статус назначения."""
        return "Распределена" if obj.assigned_at else "Ожидает распределения"

    @admin.display(description="Распределить заявку")
    def distribution_actions(self, obj: TrainingEnrollment) -> str:
        """Кнопки отправляют POST через отдельные формы вне формы списка Django."""
        if not obj.pk or obj.assigned_at:
            return "—"
        return str(
            render_to_string(
                "admin/training/trainingenrollment/distribution_actions.html",
                {"enrollment": obj},
            )
        )

    def get_urls(self) -> list[URLPattern]:
        """Добавить защищённый маршрут распределения конкретной заявки."""
        urls: list[URLPattern] = super().get_urls()
        return [
            path(
                "<int:enrollment_id>/distribute/",
                self.admin_site.admin_view(self.distribute_view),
                name="training_trainingenrollment_distribute",
            )
        ] + urls

    @method_decorator(require_http_methods(["GET", "POST"]))
    def distribute_view(self, request: HttpRequest, enrollment_id: int) -> HttpResponse:
        """Показать активных тренеров или однократно передать заявку выбранному тренеру."""
        if not is_enrollment_admin(request.user):
            raise PermissionDenied
        enrollment = get_object_or_404(self.get_queryset(request), pk=enrollment_id)
        if request.method == "POST":
            raw_id = request.POST.get("coach_id", "")
            try:
                coach_id = int(raw_id) if raw_id else None
            except ValueError:
                messages.error(request, "Выберите активного тренера.")
                return redirect(
                    "admin:training_trainingenrollment_distribute", enrollment_id
                )
            try:
                assigned = distribute_enrollment(
                    enrollment_id,
                    actor=request.user,
                    action=request.POST.get("action", ""),
                    coach_id=coach_id,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                self.log_change(
                    request,
                    assigned,
                    f"Заявка назначена тренеру {assigned.assigned_coach}.",
                )
                messages.success(
                    request, f"Заявка передана тренеру {assigned.assigned_coach}."
                )
            return redirect("admin:training_trainingenrollment_changelist")
        context: dict[str, Any] = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": f"Отдать другому тренеру: заявка №{enrollment.pk}",
            "enrollment": enrollment,
            "coaches": available_coaches().exclude(pk=enrollment.requested_coach_id),
        }
        return TemplateResponse(
            request, "admin/training/trainingenrollment/distribute.html", context
        )
