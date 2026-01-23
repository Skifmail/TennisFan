"""
Content admin configuration.
"""

from django.contrib import admin

from .models import Gallery, News, Page, Photo


class PhotoInline(admin.TabularInline):
    """Inline for photos in gallery."""

    model = Photo
    extra = 3
    fields = ("image", "caption", "order")


@admin.register(News)
class NewsAdmin(admin.ModelAdmin):
    """Admin for News model."""

    list_display = ("title", "is_published", "is_featured", "views_count", "created_at")
    list_filter = ("is_published", "is_featured")
    search_fields = ("title", "content")
    list_editable = ("is_published", "is_featured")
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "created_at"

    fieldsets = (
        (None, {"fields": ("title", "slug", "excerpt", "content")}),
        ("Медиа", {"fields": ("image",)}),
        ("Публикация", {"fields": ("is_published", "is_featured", "published_at")}),
    )


@admin.register(Gallery)
class GalleryAdmin(admin.ModelAdmin):
    """Admin for Gallery model."""

    list_display = ("title", "tournament", "photos_count", "is_published", "created_at")
    list_filter = ("is_published",)
    search_fields = ("title", "description")
    list_editable = ("is_published",)
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("tournament",)
    inlines = [PhotoInline]


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    """Admin for Photo model."""

    list_display = ("id", "gallery", "caption", "order", "created_at")
    list_filter = ("gallery",)
    list_editable = ("order",)
    raw_id_fields = ("gallery",)


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    """Admin for Page model."""

    list_display = ("title", "slug", "is_published", "show_in_footer", "order")
    list_filter = ("is_published", "show_in_footer")
    search_fields = ("title", "content")
    list_editable = ("is_published", "show_in_footer", "order")
    prepopulated_fields = {"slug": ("title",)}
