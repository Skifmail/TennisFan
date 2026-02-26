from django.contrib import admin

from .models import PlayerSkillAggregate, PlayerSkillRating


@admin.register(PlayerSkillRating)
class PlayerSkillRatingAdmin(admin.ModelAdmin):
    list_display = ("match", "from_player", "to_player", "created_at")
    list_filter = ("created_at",)
    search_fields = ("from_player__user__email", "to_player__user__email")
    raw_id_fields = ("match", "from_player", "to_player")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PlayerSkillAggregate)
class PlayerSkillAggregateAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "metric_name",
        "average_raw",
        "average_weighted",
        "votes_count",
        "updated_at",
    )
    list_filter = ("metric_name",)
    search_fields = ("player__user__email",)
    raw_id_fields = ("player",)
    readonly_fields = ("updated_at",)
