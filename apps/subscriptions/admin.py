from django.contrib import admin

from .models import RegionalTierPrice, SubscriptionTier, UserSubscription


class RegionalTierPriceInline(admin.TabularInline):
    model = RegionalTierPrice
    extra = 1
    fields = ("name", "price", "original_price", "original_price_ends_at")


@admin.register(SubscriptionTier)
class SubscriptionTierAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "price",
        "original_price",
        "original_price_ends_at",
        "first_subscription_one_ruble",
        "max_tournaments",
        "is_unlimited",
        "one_day_tournament_discount",
        "has_badge",
    )
    list_editable = (
        "price",
        "original_price",
        "original_price_ends_at",
        "first_subscription_one_ruble",
        "max_tournaments",
        "is_unlimited",
        "one_day_tournament_discount",
    )
    ordering = ("price",)
    inlines = [RegionalTierPriceInline]


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "tier",
        "status_display",
        "tournaments_registered_count",
        "registrations_limit_display",
        "end_date",
        "cancelled_at",
    )
    list_filter = ("tier", "is_active", "end_date")
    search_fields = ("user__username", "user__email", "user__last_name")
    readonly_fields = ("tournaments_registered_count", "cancelled_at")
    autocomplete_fields = ("user",)

    def registrations_limit_display(self, obj):
        """Отображение лимита регистраций."""
        if obj.tier.is_unlimited:
            return "Безлимит"
        return f"{obj.tier.max_tournaments}"

    registrations_limit_display.short_description = "Лимит"
