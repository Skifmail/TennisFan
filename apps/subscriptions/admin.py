from typing import Any, cast

from django.contrib import admin
from django.http import HttpRequest

from .models import RegionalTierPrice, SubscriptionTier, UserSubscription


class RegionalTierPriceInline(admin.TabularInline):
    model = RegionalTierPrice
    extra = 1
    fields = ("name", "price", "original_price", "original_price_ends_at")


@admin.register(SubscriptionTier)
class SubscriptionTierAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "card_theme",
        "is_popular",
        "duration_days",
        "sort_order",
        "is_visible",
        "price",
        "original_price",
        "original_price_ends_at",
        "first_subscription_one_ruble",
        "max_tournaments",
        "is_unlimited",
        "one_day_tournament_discount",
        "has_badge",
    )
    exclude = ("name",)
    list_editable = (
        "card_theme",
        "is_popular",
        "duration_days",
        "sort_order",
        "is_visible",
        "price",
        "original_price",
        "original_price_ends_at",
        "first_subscription_one_ruble",
        "max_tournaments",
        "is_unlimited",
        "one_day_tournament_discount",
    )
    search_fields = ("display_name",)
    list_filter = (
        "is_popular",
        "is_visible",
        "is_unlimited",
        "has_badge",
        "has_private_chat",
    )
    ordering = ("sort_order", "price", "id")
    inlines = [RegionalTierPriceInline]

    def has_delete_permission(
        self, request: HttpRequest, obj: SubscriptionTier | None = None
    ) -> bool:
        """Проверить возможность удаления тарифа.

        Args:
            request: Текущий HTTP-запрос администратора.
            obj: Объект тарифа или ``None`` для общего списка.

        Returns:
            bool: ``False`` для системных тарифов, иначе стандартное поведение.
        """
        if obj and obj.is_system_tier:
            return False
        return cast(bool, super().has_delete_permission(request, obj))

    def get_actions(self, request: HttpRequest) -> dict[str, tuple[Any, str, str]]:
        """Вернуть доступные массовые действия для списка тарифов.

        Args:
            request: Текущий HTTP-запрос администратора.

        Returns:
            dict[str, tuple]: Доступные действия админки без массового удаления.
        """
        actions = cast(
            dict[str, tuple[Any, str, str]],
            super().get_actions(request),
        )
        actions.pop("delete_selected", None)
        return actions


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "tier",
        "purchase_city",
        "status_display",
        "tournament_registration_balance",
        "registrations_limit_display",
        "end_date",
        "cancelled_at",
    )
    list_filter = ("tier", "is_active", "end_date")
    search_fields = ("user__username", "user__email", "user__last_name")
    readonly_fields = (
        "tournament_registration_balance",
        "cancelled_at",
        "purchase_city",
    )
    autocomplete_fields = ("user",)

    def registrations_limit_display(self, obj: UserSubscription) -> str:
        """Отображение лимита регистраций."""
        if obj.tier.is_unlimited:
            return "Безлимит"
        return f"{obj.tier.max_tournaments}"

    registrations_limit_display.short_description = "Лимит"
