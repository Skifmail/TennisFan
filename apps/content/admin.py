"""
Content admin configuration.
"""

from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.shortcuts import redirect
from django.urls import reverse

from apps.comments.models import Comment

from .forms import RulesSectionAdminForm
from .models import (
    AboutUs,
    ContactItem,
    ContactPage,
    Gallery,
    LiveStream,
    News,
    NewsPhoto,
    Photo,
    RulesSection,
    StringerCompany,
    StringerCompanyContact,
    StringerCompanyPhoto,
    StringerCompanyRating,
    StringerPage,
    Video,
    VideoPage,
)


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
        (
            "Медиа",
            {
                "fields": ("image",),
                "description": "Главное изображение. Дополнительные фото — в блоке «Фото новостей» ниже.",
            },
        ),
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
            return redirect(reverse("admin:content_aboutus_change", args=[obj.pk]))
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

    form = RulesSectionAdminForm
    list_display = ("title", "slug", "updated_at")
    list_display_links = ("title", "slug")
    search_fields = ("title", "body")
    readonly_fields = ("slug", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": ("slug", "title", "body"),
                "description": (
                    "Содержимое отображается на странице «Правила». "
                    "Если поле пустое в БД, при открытии подставляется текст "
                    "из шаблона-фолбэка — сохраните форму, чтобы зафиксировать "
                    "его в базе. Для раздела «Правила тенниса» ссылки на PDF "
                    "не редактируются — они закреплены на странице."
                ),
            },
        ),
        ("Служебное", {"fields": ("updated_at",)}),
    )


class LiveStreamInline(admin.TabularInline):
    """Inline для прямых трансляций."""

    model = LiveStream
    extra = 1
    fields = ("title", "url", "platform", "is_active", "order")
    ordering = ("order", "-created_at")


class VideoInline(admin.TabularInline):
    """Inline для видео в плейлисте."""

    model = Video
    extra = 1
    fields = ("title", "url", "platform", "is_published", "order")
    ordering = ("order", "-created_at")


@admin.register(VideoPage)
class VideoPageAdmin(admin.ModelAdmin):
    """Админ для страницы «Видео» — настройки и контент в одном месте."""

    list_display = ("__str__", "live_streams_title", "playlist_title", "updated_at")
    fieldsets = (
        (
            "Заголовки блоков",
            {
                "fields": ("live_streams_title", "playlist_title"),
                "description": "Заголовки отображаются на странице «Видео».",
            },
        ),
    )
    inlines = [LiveStreamInline, VideoInline]

    def has_add_permission(self, request) -> bool:
        """Only one VideoPage instance allowed."""
        return not VideoPage.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        """Prevent deletion of singleton."""
        return False

    def changelist_view(self, request, extra_context=None):
        """Redirect to change view for singleton."""
        obj = VideoPage.objects.first()
        if obj and not request.path.endswith("/change/"):
            return redirect(reverse("admin:content_videopage_change", args=[obj.pk]))
        return super().changelist_view(request, extra_context)


# LiveStream и Video управляются через inline формы в VideoPageAdmin
# Отдельные разделы убраны для упрощения интерфейса


# ---------------------------------------------------------------------------
# Стрингеры
# ---------------------------------------------------------------------------


class StringerCompanyContactInline(admin.TabularInline):
    """Inline для контактов компании стрингеров (Телефон, Telegram, WhatsApp, MAX)."""

    model = StringerCompanyContact
    extra = 1
    fields = ("contact_type", "value", "order")
    verbose_name = "Контакт"
    verbose_name_plural = "Контакты"


class StringerCompanyPhotoInline(admin.TabularInline):
    """Inline для фото компании стрингеров."""

    model = StringerCompanyPhoto
    extra = 1
    fields = ("image", "caption", "order")
    verbose_name = "Фото"
    verbose_name_plural = "Фото компании"


class StringerCompanyRatingInline(admin.TabularInline):
    """Inline для оценок компании (только просмотр)."""

    model = StringerCompanyRating
    extra = 0
    readonly_fields = ("user", "score", "comment", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class StringerCompanyInline(admin.StackedInline):
    """Inline для компаний стрингеров в StringerPageAdmin."""

    model = StringerCompany
    extra = 0
    fieldsets = (
        (
            "Основная информация",
            {
                "fields": ("name", "address", "price", "description"),
            },
        ),
        (
            "Настройки",
            {
                "fields": ("is_active", "order"),
            },
        ),
    )
    # Контакты и фото — через редактирование конкретной компании
    # или использовать кастомный шаблон для вложенных inlines


@admin.register(StringerPage)
class StringerPageAdmin(admin.ModelAdmin):
    """Админ для страницы «Стрингеры» — настройки и компании в одном месте."""

    list_display = ("__str__", "is_enabled", "updated_at")
    fieldsets = (
        (
            "Настройки страницы",
            {
                "fields": ("title", "description", "is_enabled"),
                "description": "Настройки отображаются на странице «Стрингеры».",
            },
        ),
        ("Служебное", {"fields": ("updated_at",)}),
    )
    readonly_fields = ("updated_at",)
    inlines = [StringerCompanyInline]

    def has_add_permission(self, request) -> bool:
        """Prevent adding new instances (singleton)."""
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        """Prevent deletion of singleton."""
        return False

    def changelist_view(self, request, extra_context=None):
        """Redirect to change view for singleton."""
        obj = StringerPage.objects.first()
        if obj and not request.path.endswith("/change/"):
            return redirect(reverse("admin:content_stringerpage_change", args=[obj.pk]))
        return super().changelist_view(request, extra_context)


@admin.register(StringerCompany)
class StringerCompanyAdmin(admin.ModelAdmin):
    """Админ для компаний стрингеров — отдельное редактирование с фото и контактами."""

    list_display = (
        "name",
        "address",
        "is_active",
        "order",
        "rating_display",
        "created_at",
    )
    list_filter = ("is_active", "created_at", "stringer_page")
    search_fields = ("name", "address", "contact_items__value", "description")
    list_editable = ("is_active", "order")
    inlines = [
        StringerCompanyContactInline,
        StringerCompanyPhotoInline,
        StringerCompanyRatingInline,
    ]

    fieldsets = (
        (
            "Основная информация",
            {
                "fields": ("stringer_page", "name", "address", "price", "description"),
            },
        ),
        (
            "Настройки",
            {
                "fields": ("is_active", "order"),
            },
        ),
        (
            "Служебное",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )
    readonly_fields = ("created_at", "updated_at")

    def rating_display(self, obj):
        """Отображение рейтинга в списке."""
        avg = obj.get_average_rating()
        count = obj.get_rating_count()
        if avg is not None:
            return f"{avg:.1f} ⭐ ({count})"
        return "Нет оценок"

    rating_display.short_description = "Рейтинг"


@admin.register(StringerCompanyRating)
class StringerCompanyRatingAdmin(admin.ModelAdmin):
    """Отдельный список всех отзывов (оценок с комментариями) о компаниях стрингеров."""

    list_display = ("company", "user", "score", "comment_preview", "created_at")
    list_filter = ("score", "company", "created_at")
    search_fields = (
        "comment",
        "user__email",
        "user__first_name",
        "user__last_name",
        "company__name",
    )
    readonly_fields = (
        "company",
        "user",
        "score",
        "comment",
        "created_at",
        "updated_at",
    )
    raw_id_fields = ("user",)

    def comment_preview(self, obj):
        if not obj.comment:
            return "—"
        return obj.comment[:80] + "…" if len(obj.comment) > 80 else obj.comment

    comment_preview.short_description = "Комментарий"
