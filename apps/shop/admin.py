"""
Shop admin configuration.
"""

from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from .models import Product, ProductPhoto, PurchaseRequest, ShopPage


class ProductPhotoInline(admin.TabularInline):
    """Inline для фото товара (до 10)."""

    model = ProductPhoto
    extra = 1
    fields = ("image", "order")
    ordering = ("order", "id")
    max_num = 10

    def get_queryset(self, request):
        return super().get_queryset(request).order_by("order")


@admin.register(ShopPage)
class ShopPageAdmin(admin.ModelAdmin):
    """Админ для страницы «Магазин»."""

    list_display = ("__str__", "updated_at")
    fieldsets = (
        (
            "Содержимое",
            {
                "fields": ("intro_text",),
                "description": "Описание магазина. Поддерживается Markdown.",
            },
        ),
    )

    def has_add_permission(self, request) -> bool:
        return not ShopPage.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def changelist_view(self, request, extra_context=None):
        obj = ShopPage.objects.first()
        if obj and not request.path.endswith("/change/"):
            return redirect(reverse("admin:shop_shoppage_change", args=[obj.pk]))
        return super().changelist_view(request, extra_context)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """Админ для товаров."""

    list_display = ("name", "size", "quantity", "price", "order", "created_at")
    list_filter = ("created_at",)
    search_fields = ("name", "description")
    list_editable = ("order",)
    ordering = ("order", "id")
    inlines = [ProductPhotoInline]

    fieldsets = (
        (None, {"fields": ("name", "size", "quantity", "description", "price", "order")}),
    )


@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(admin.ModelAdmin):
    """Админ для заявок на покупку."""

    list_display = ("product", "last_name", "first_name", "contact_phone", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("first_name", "last_name", "contact_phone", "product__name")
    list_editable = ("status",)
    raw_id_fields = ("product", "user")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"

    fieldsets = (
        ("Товар", {"fields": ("product",)}),
        ("Заявитель", {"fields": ("user", "first_name", "last_name", "contact_phone", "comment")}),
        ("Статус", {"fields": ("status",)}),
        ("Даты", {"fields": ("created_at", "updated_at")}),
    )
