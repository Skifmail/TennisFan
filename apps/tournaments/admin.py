"""
Tournaments admin configuration.
"""

from django.contrib import admin

from .models import HeadToHead, Match, MatchResultProposal, SeasonRating, Tournament


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    """Admin for Tournament model."""

    list_display = (
        "name",
        "city",
        "category",
        "gender",
        "duration",
        "tournament_type",
        "status",
        "start_date",
        "points_winner",
        "max_participants",
    )
    list_filter = ("city", "category", "gender", "duration", "tournament_type", "status")
    search_fields = ("name", "description")
    list_editable = ("status",)
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("participants",)
    date_hierarchy = "start_date"

    fieldsets = (
        ("Базовая информация", {"fields": ("name", "slug", "description", "image")}),
        (
            "Стоимость и Тип",
            {
                "fields": (
                    "entry_fee",
                    "is_one_day",
                )
            },
        ),
        (
            "Категории",
            {
                "fields": (
                    "city",
                    "category",
                    "gender",
                    "duration",
                    "tournament_type",
                    "status",
                )
            },
        ),
        ("Даты", {"fields": ("start_date", "end_date", "registration_deadline")}),
        ("Очки и ограничения", {"fields": ("points_winner", "points_loser", "max_participants")}),
        ("Участники", {"fields": ("participants",)}),
    )

    class Media:
        js = ('js/admin_tournament.js',)


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    """Admin for Match model."""

    list_display = (
        "tournament",
        "player1",
        "player2",
        "score_display",
        "winner",
        "status",
        "scheduled_datetime",
    )
    list_filter = ("status", "tournament__city", "tournament")
    search_fields = (
        "player1__user__first_name",
        "player1__user__last_name",
        "player2__user__first_name",
        "player2__user__last_name",
    )
    raw_id_fields = ("player1", "player2", "winner", "court")
    date_hierarchy = "scheduled_datetime"

    fieldsets = (
        ("Турнир", {"fields": ("tournament", "court", "round_name")}),
        ("Игроки", {"fields": ("player1", "player2", "winner")}),
        (
            "Счёт",
            {
                "fields": (
                    ("player1_set1", "player2_set1"),
                    ("player1_set2", "player2_set2"),
                    ("player1_set3", "player2_set3"),
                )
            },
        ),
        ("Очки", {"fields": ("points_player1", "points_player2")}),
        ("Время", {"fields": ("scheduled_datetime", "completed_datetime", "status")}),
    )


@admin.register(HeadToHead)
class HeadToHeadAdmin(admin.ModelAdmin):
    """Admin for HeadToHead model."""

    list_display = ("player1", "player1_wins", "player2_wins", "player2")
    raw_id_fields = ("player1", "player2")


@admin.register(SeasonRating)
class SeasonRatingAdmin(admin.ModelAdmin):
    """Admin for SeasonRating model."""

    list_display = ("player", "season", "category", "points", "rank")
    list_filter = ("season", "category")
    search_fields = ("player__user__first_name", "player__user__last_name")
    raw_id_fields = ("player",)
    list_editable = ("points", "rank")


@admin.register(MatchResultProposal)
class MatchResultProposalAdmin(admin.ModelAdmin):
    """Admin for match proposals."""

    list_display = ("match", "proposer", "result", "status", "created_at")
    list_filter = ("status", "result")
    search_fields = ("match__tournament__name", "proposer__user__email")
