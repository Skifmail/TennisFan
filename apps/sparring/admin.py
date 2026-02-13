"""
Sparring admin configuration.
"""

from django.contrib import admin

from .models import SparringRequest, SparringResponse


@admin.register(SparringRequest)
class SparringRequestAdmin(admin.ModelAdmin):
    """Admin for SparringRequest model."""

    list_display = (
        "player",
        "city",
        "desired_category",
        "status",
        "created_at",
        "response_count",
    )
    list_filter = ("city", "desired_category", "status", "created_at")
    search_fields = (
        "player__user__first_name",
        "player__user__last_name",
        "description",
        "city",
        "preferred_location",
    )
    list_editable = ("status",)
    raw_id_fields = ("player",)
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Основная информация", {"fields": ("player", "city", "status")}),
        (
            "Предпочтения",
            {
                "fields": (
                    "desired_category",
                    "desired_partner_age_min",
                    "desired_partner_age_max",
                    "preferred_location",
                    "preferred_days",
                    "preferred_time",
                )
            },
        ),
        ("Описание", {"fields": ("description",)}),
        ("Даты", {"fields": ("created_at", "updated_at")}),
    )

    def response_count(self, obj):
        """Количество откликов на заявку."""
        return obj.responses.count()

    response_count.short_description = "Откликов"


@admin.register(SparringResponse)
class SparringResponseAdmin(admin.ModelAdmin):
    list_display = (
        "sparring_request",
        "respondent",
        "contact_method",
        "status",
        "created_at",
        "has_match",
    )
    list_filter = ("contact_method", "status", "created_at")
    search_fields = (
        "sparring_request__player__user__first_name",
        "sparring_request__player__user__last_name",
        "respondent__user__first_name",
        "respondent__user__last_name",
    )
    raw_id_fields = ("sparring_request", "respondent")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")

    def has_match(self, obj):
        """Проверка, создан ли матч из этого отклика."""
        return obj.matches.exists()

    has_match.boolean = True
    has_match.short_description = "Матч создан"
