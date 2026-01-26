from django.contrib import admin
from .models import SubscriptionTier, UserSubscription

@admin.register(SubscriptionTier)
class SubscriptionTierAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'price', 
        'max_tournaments', 'is_unlimited', 
        'one_day_tournament_discount',
        'has_badge'
    )
    list_editable = ('price', 'max_tournaments', 'is_unlimited', 'one_day_tournament_discount')
    ordering = ('price',)

@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'user', 
        'tier', 
        'status_display', 
        'tournaments_registered_count', 
        'registrations_limit_display',
        'end_date'
    )
    list_filter = ('tier', 'is_active', 'end_date')
    search_fields = ('user__username', 'user__email', 'user__last_name')
    readonly_fields = ('tournaments_registered_count',)
    autocomplete_fields = ('user',)

    def registrations_limit_display(self, obj):
        if obj.tier.is_unlimited:
            return "Безлимит"
        return f"{obj.tier.max_tournaments}"
    registrations_limit_display.short_description = "Лимит"
