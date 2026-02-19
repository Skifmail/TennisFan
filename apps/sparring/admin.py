"""
Sparring admin configuration.
"""

from django.contrib import admin

from .models import (
    DoublesJoinRequest,
    DoublesJoinRequestMember,
    DoublesMatchRequest,
    DoublesTeam,
    DoublesTeamMember,
    SparringRequest,
    SparringResponse,
)


@admin.register(SparringRequest)
class SparringRequestAdmin(admin.ModelAdmin):
    """Admin for SparringRequest model."""

    list_display = (
        "player",
        "city",
        "match_type",
        "preferred_gender",
        "desired_category",
        "is_friendly",
        "status",
        "created_at",
        "response_count",
    )
    list_filter = (
        "city",
        "match_type",
        "preferred_gender",
        "desired_category",
        "is_friendly",
        "status",
        "created_at",
    )
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
        (
            "Основная информация",
            {
                "fields": (
                    "player",
                    "city",
                    "match_type",
                    "preferred_gender",
                    "status",
                    "is_friendly",
                )
            },
        ),
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


# ---------------------------------------------------------------------------
# Парный спарринг 2×2
# ---------------------------------------------------------------------------


class DoublesTeamMemberInline(admin.TabularInline):
    model = DoublesTeamMember
    raw_id_fields = ("player",)
    extra = 0


class DoublesTeamInline(admin.TabularInline):
    model = DoublesTeam
    extra = 0
    show_change_link = True


class DoublesJoinRequestMemberInline(admin.TabularInline):
    model = DoublesJoinRequestMember
    raw_id_fields = ("player",)
    extra = 0


@admin.register(DoublesMatchRequest)
class DoublesMatchRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "created_by", "city", "status", "created_at", "match")
    list_filter = ("status", "is_friendly", "created_at")
    search_fields = (
        "created_by__user__first_name",
        "created_by__user__last_name",
        "city",
        "description",
    )
    raw_id_fields = ("created_by", "match")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "confirmed_at")
    inlines = (DoublesTeamInline,)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "status",
                    "created_by",
                    "city",
                    "preferred_gender",
                    "is_friendly",
                    "description",
                )
            },
        ),
        ("Даты", {"fields": ("created_at", "updated_at", "confirmed_at")}),
        ("Матч", {"fields": ("match",)}),
    )


@admin.register(DoublesTeam)
class DoublesTeamAdmin(admin.ModelAdmin):
    list_display = ("id", "match_request", "side", "member_count")
    list_filter = ("side",)
    raw_id_fields = ("match_request",)
    inlines = (DoublesTeamMemberInline,)

    def member_count(self, obj):
        return obj.members.count()

    member_count.short_description = "Участников"


@admin.register(DoublesJoinRequest)
class DoublesJoinRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "match_request",
        "target_side",
        "created_by",
        "status",
        "created_at",
    )
    list_filter = ("status", "target_side", "created_at")
    raw_id_fields = ("match_request", "created_by")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "processed_at")
    inlines = (DoublesJoinRequestMemberInline,)
