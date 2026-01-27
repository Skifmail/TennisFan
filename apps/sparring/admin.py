"""
Sparring admin configuration.
"""

from django.contrib import admin

from .models import SparringRequest, SparringResponse


@admin.register(SparringRequest)
class SparringRequestAdmin(admin.ModelAdmin):
    """Admin for SparringRequest model."""

    list_display = ("player", "city", "desired_category", "status", "created_at")
    list_filter = ("city", "desired_category", "status")
    search_fields = ("player__user__first_name", "player__user__last_name", "description")
    list_editable = ("status",)
    raw_id_fields = ("player",)
    date_hierarchy = "created_at"


@admin.register(SparringResponse)
class SparringResponseAdmin(admin.ModelAdmin):
    list_display = ("sparring_request", "respondent", "contact_method", "created_at")
    list_filter = ("contact_method",)
    raw_id_fields = ("sparring_request", "respondent")
    date_hierarchy = "created_at"
