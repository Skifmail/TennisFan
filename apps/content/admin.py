"""
Content admin configuration.
"""

from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.shortcuts import redirect
from django.urls import reverse

from apps.comments.models import Comment

from .models import AboutUs, ContactItem, ContactPage, Gallery, News, NewsPhoto, Page, Photo, RulesSection


class CommentInline(GenericTabularInline):
    """Inline для комментариев на странице «О нас»."""

    model = Comment
    ct_field = "content_type"
    ct_fk_field = "object_id"
    extra = 0
    fields = ("author", "text", "is_approved", "created_at")
    readonly_fields = ("created_at",)
    raw_id_fields = ("author",)


class NewsPhotoInline(admin.TabularInline):
    """Inline для галереи фото к новости."""

    model = NewsPhoto
    extra = 2
    fields = ("image", "caption", "order")


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
    inlines = [NewsPhotoInline]

    fieldsets = (
        (None, {"fields": ("title", "slug", "excerpt", "content")}),
        ("Медиа", {"fields": ("image",), "description": "Главное изображение. Дополнительные фото — в блоке «Фото новостей» ниже."}),
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


@admin.register(AboutUs)
class AboutUsAdmin(admin.ModelAdmin):
    """Admin for AboutUs singleton. Заголовок «О НАС» фиксирован на странице."""

    list_display = ("__str__", "subtitle", "updated_at")
    fieldsets = (
        (
            "Контент",
            {
                "fields": ("subtitle", "image", "body"),
                "description": "Заголовок «О НАС» отображается на странице автоматически.",
            },
        ),
    )
    inlines = [CommentInline]

    def has_add_permission(self, request) -> bool:
        """Only one AboutUs instance allowed."""
        return not AboutUs.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        """Prevent deletion of singleton."""
        return False

    def changelist_view(self, request, extra_context=None):
        """Redirect to change view for singleton."""
        obj = AboutUs.objects.first()
        if obj and not request.path.endswith("/change/"):
            return redirect(
                reverse("admin:content_aboutus_change", args=[obj.pk])
            )
        return super().changelist_view(request, extra_context)


class ContactItemInline(admin.TabularInline):
    """Inline для контактов — редактируются вместе со страницей."""

    model = ContactItem
    extra = 1
    fields = ("item_type", "label", "value", "url", "order")
    ordering = ("order", "id")


@admin.register(ContactPage)
class ContactPageAdmin(admin.ModelAdmin):
    """Объединённый админ «Контакты» — текст и список контактов в одном месте."""

    list_display = ("__str__", "updated_at")
    fieldsets = (
        (
            "Текст перед контактами",
            {
                "fields": ("intro_text",),
                "description": "Произвольный текст (приветствие, описание). Поддерживается Markdown.",
            },
        ),
    )
    inlines = [ContactItemInline]

    def has_add_permission(self, request) -> bool:
        return not ContactPage.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def changelist_view(self, request, extra_context=None):
        obj = ContactPage.objects.first()
        if obj and not request.path.endswith("/change/"):
            return redirect(reverse("admin:content_contactpage_change", args=[obj.pk]))
        return super().changelist_view(request, extra_context)


@admin.register(RulesSection)
class RulesSectionAdmin(admin.ModelAdmin):
    """Редактирование разделов правил (теннис, турниры, пользование сайтом)."""

    list_display = ("title", "slug", "updated_at")
    list_display_links = ("title", "slug")
    search_fields = ("title", "body")
    readonly_fields = ("slug", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": ("slug", "title", "body"),
                "description": "Содержимое отображается на странице «Правила». Для раздела «Правила тенниса» ссылки на PDF не редактируются — они закреплены на странице.",
            },
        ),
        ("Служебное", {"fields": ("updated_at",)}),
    )


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    """Admin for Page model."""

    list_display = ("title", "slug", "is_published", "show_in_footer", "order")
    list_filter = ("is_published", "show_in_footer")
    search_fields = ("title", "content")
    list_editable = ("is_published", "show_in_footer", "order")
    prepopulated_fields = {"slug": ("title",)}
