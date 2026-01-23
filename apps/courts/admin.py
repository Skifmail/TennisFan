"""
Courts admin configuration.
"""

from django.contrib import admin

from .models import Court


@admin.register(Court)
class CourtAdmin(admin.ModelAdmin):
    """Admin for Court model."""

    list_display = (
        "name",
        "city",
        "surface",
        "courts_count",
        "is_indoor",
        "price_per_hour",
        "is_active",
    )
    list_filter = ("city", "surface", "is_indoor", "has_lighting", "is_active")
    search_fields = ("name", "address")
    list_editable = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}

    fieldsets = (
        (None, {"fields": ("name", "slug", "city", "address", "description")}),
        ("Характеристики", {"fields": ("surface", "courts_count", "has_lighting", "is_indoor")}),
        ("Контакты", {"fields": ("phone", "whatsapp", "website")}),
        ("Карта", {"fields": ("latitude", "longitude")}),
        ("Цена и фото", {"fields": ("price_per_hour", "image")}),
        ("Статус", {"fields": ("is_active",)}),
    )
