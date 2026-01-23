"""
Comments admin configuration.
"""

from django.contrib import admin

from .models import Comment


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    """Admin for Comment model."""

    list_display = (
        "author",
        "content_type",
        "object_id",
        "rating_agreement",
        "rating_judging",
        "is_approved",
        "created_at",
    )
    list_filter = ("content_type", "is_approved", "rating_agreement", "rating_judging")
    search_fields = ("text", "author__user__first_name", "author__user__last_name")
    list_editable = ("is_approved",)
    raw_id_fields = ("author",)
    date_hierarchy = "created_at"
    readonly_fields = ("content_type", "object_id", "created_at", "updated_at")

    fieldsets = (
        ("Связь", {"fields": ("content_type", "object_id", "author")}),
        ("Контент", {"fields": ("text",)}),
        ("Оценки", {"fields": ("rating_agreement", "rating_judging")}),
        ("Статус", {"fields": ("is_approved",)}),
        ("Даты", {"fields": ("created_at", "updated_at")}),
    )
