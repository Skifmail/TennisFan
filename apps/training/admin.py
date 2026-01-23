"""
Training admin configuration.
"""

from django.contrib import admin

from .models import Coach, Training, TrainingEnrollment


@admin.register(Coach)
class CoachAdmin(admin.ModelAdmin):
    """Admin for Coach model."""

    list_display = ("name", "city", "experience_years", "specialization", "is_active")
    list_filter = ("city", "is_active")
    search_fields = ("name", "bio", "specialization")
    list_editable = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}


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

    list_display = ("training", "player", "status", "created_at")
    list_filter = ("status", "training")
    search_fields = ("player__user__first_name", "player__user__last_name")
    list_editable = ("status",)
    raw_id_fields = ("training", "player")
    date_hierarchy = "created_at"
