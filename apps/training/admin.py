"""
Training admin configuration.
"""

import logging

from django.contrib import admin, messages
from django.utils.html import format_html

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

    list_display = ("name", "user", "city", "experience_years", "specialization", "is_active")
    list_filter = ("city", "is_active")
    search_fields = ("name", "bio", "specialization")
    list_editable = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}


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
        messages.success(request, f"Одобрено заявок: {ok}. Тренеры добавлены в «Наши тренеры».")
    if err:
        messages.error(request, f"Не удалось одобрить заявок: {err}. См. лог.")


@admin.action(description="Отклонить заявки")
def reject_coach_applications(modeladmin, request, queryset):
    updated = queryset.filter(status=CoachApplicationStatus.PENDING).update(
        status=CoachApplicationStatus.REJECTED
    )
    if updated:
        messages.success(request, f"Отклонено заявок: {updated}.")


@admin.register(CoachApplication)
class CoachApplicationAdmin(admin.ModelAdmin):
    """Заявки «Стать тренером». Одобренные превращаются в Coach."""

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
    search_fields = ("name", "city", "applicant_name", "applicant_email", "specialization")
    list_display_links = ("name",)
    actions = [approve_coach_applications, reject_coach_applications]
    readonly_fields = ("status", "coach", "created_at", "updated_at")

    fieldsets = (
        ("Заявитель", {"fields": ("applicant_name", "applicant_email", "applicant_phone")}),
        (None, {"fields": ("name", "photo", "bio", "experience_years", "specialization", "city")}),
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

    list_display = (
        "title",
        "training_type",
        "skill_level",
        "coach",
        "city",
        "price",
        "is_active",
        "is_featured",
    )
    list_filter = ("training_type", "skill_level", "city", "is_active", "is_featured")
    search_fields = ("title", "description")
    list_editable = ("is_active", "is_featured")
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("coach", "court")


@admin.register(TrainingEnrollment)
class TrainingEnrollmentAdmin(admin.ModelAdmin):
    """Admin for TrainingEnrollment model."""

    list_display = ("training", "full_name", "email", "player", "desired_court", "status", "created_at")
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
