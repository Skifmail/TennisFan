"""
Training admin configuration.
"""

import logging

from django.contrib import admin, messages
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.html import format_html

from apps.training.coach_defaults import coach_defaults_from_user
from apps.users.models import User

from .forms import AdminTrainingForm, CoachAdminForm
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
        "experience_years",
        "specialization",
        "is_active",
    )
    list_filter = ("city", "is_active")
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

    class Media:
        js = ("js/city_autocomplete.js",)

    list_display = (
        "name",
        "city",
        "applicant_user",
        "applicant_name",
        "applicant_email",
        "status_badge",
        "coach_link",
        "created_at",
    )
    list_filter = ("status", "city")
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
        "types_display",
        "levels_display",
        "coach",
        "courts_display",
        "city",
        "price_range_display",
        "court_price_range_display",
        "is_active",
        "is_featured",
    )
    list_filter = ("city", "is_active", "is_featured")
    search_fields = ("title", "description")
    list_editable = ("is_active", "is_featured")
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
        ("Настройки", {"fields": ("is_active", "is_featured")}),
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
    """Admin for TrainingEnrollment model."""

    list_display = (
        "training",
        "full_name",
        "email",
        "player",
        "desired_court",
        "status",
        "created_at",
    )
    list_filter = ("status", "training")
    search_fields = (
        "full_name",
        "email",
        "telegram",
        "player__user__first_name",
        "player__user__last_name",
    )
    list_editable = ("status",)
    raw_id_fields = ("training", "player")
    date_hierarchy = "created_at"
