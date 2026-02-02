"""
Tournaments admin configuration.
"""

from django.contrib import admin, messages

from .fan import generate_bracket
from .round_robin import generate_bracket as generate_round_robin_bracket
from .proposal_service import apply_proposal
from .models import (
    HeadToHead,
    Match,
    MatchResultProposal,
    SeasonRating,
    Tournament,
    TournamentPlayerResult,
    TournamentTeam,
)


@admin.action(description="Подтвердить результат матча")
def accept_proposal_action(modeladmin, request, queryset):
    """Применить выбранные заявки к матчам (подтвердить от имени админа)."""
    count = 0
    for p in queryset.filter(status=Match.ProposalStatus.PENDING):
        match = p.match
        if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
            continue
        apply_proposal(p)
        count += 1
    if count:
        messages.success(request, f"Подтверждено заявок: {count}.")
    else:
        messages.warning(request, "Нет заявок для подтверждения (или матчи уже завершены).")


@admin.action(description="Сформировать сетку FAN")
def generate_fan_bracket_action(modeladmin, request, queryset):
    for t in queryset:
        ok, msg = generate_bracket(t)
        if ok:
            messages.success(request, f"{t.name}: {msg}")
        else:
            messages.warning(request, f"{t.name}: {msg}")


@admin.action(description="Сформировать сетку (круговой)")
def generate_round_robin_bracket_action(modeladmin, request, queryset):
    for t in queryset:
        ok, msg = generate_round_robin_bracket(t)
        if ok:
            messages.success(request, f"{t.name}: {msg}")
        else:
            messages.warning(request, f"{t.name}: {msg}")


class TournamentTeamInline(admin.TabularInline):
    model = TournamentTeam
    extra = 0
    raw_id_fields = ("player1", "player2")
    verbose_name = "Команда"
    verbose_name_plural = "Команды"


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    inlines = [TournamentTeamInline]

    """Admin for Tournament model.

    Страница добавления турнира: базовая информация + выбор формата.
    Поля формата появляются динамически при выборе (FAN и др.).
    """

    list_display = (
        "name",
        "city",
        "format",
        "variant",
        "duration",
        "tournament_type",
        "status",
        "bracket_generated",
        "start_date",
        "max_participants",
        "max_teams",
    )
    list_filter = (
        "city",
        "category",
        "gender",
        "duration",
        "tournament_type",
        "format",
        "variant",
        "status",
        "bracket_generated",
    )
    search_fields = ("name", "description")
    list_editable = ("status",)
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("participants",)
    date_hierarchy = "start_date"
    actions = [generate_fan_bracket_action, generate_round_robin_bracket_action]

    fieldsets = (
        ("Базовая информация", {"fields": ("name", "slug", "description", "image")}),
        ("Формат турнира", {"fields": ("format", "variant")}),
        (
            "Общие поля (FAN и Круговой)",
            {
                "fields": (
                    "entry_fee",
                    "is_one_day",
                    "city",
                    "category",
                    "gender",
                    "duration",
                    "tournament_type",
                    "status",
                    "start_date",
                    "end_date",
                    "registration_deadline",
                    "max_participants",
                    "max_teams",
                    "bracket_generated",
                    "match_days_per_round",
                    "participants",
                ),
                "classes": ("format-fan-section", "format-round-robin-section"),
            },
        ),
        (
            "FAN: очки за раунд",
            {
                "fields": (
                    "fan_points_r1",
                    "fan_points_r2",
                    "fan_points_sf",
                    "fan_points_final",
                    "fan_points_winner",
                ),
                "description": "Очки начисляются при вылете или в конце турнира.",
                "classes": ("format-fan-section",),
            },
        ),
        (
            "Круговой: формат матча",
            {
                "fields": ("match_format",),
                "classes": ("format-round-robin-section",),
                "description": "Формат матча влияет на тай-брейки и подсчёт очков в таблице.",
            },
        ),
    )

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        """Добавить пустой выбор для формата на странице добавления."""
        if db_field.name == "format":
            resolver_match = getattr(request, "resolver_match", None)
            is_add_page = "/add/" in (request.path or "") or (
                resolver_match and "add" in (getattr(resolver_match, "url_name", "") or "")
            )
            if is_add_page:
                kwargs["choices"] = [("", "---------")] + list(db_field.choices)
                kwargs["initial"] = ""
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    class Media:
        js = ("js/admin_tournament.js",)


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    """Admin for Match model."""

    list_display = (
        "tournament",
        "round_name",
        "round_index",
        "round_order",
        "is_consolation",
        "player1",
        "player2",
        "team1",
        "team2",
        "score_display",
        "winner",
        "status",
        "deadline",
    )
    list_filter = ("status", "is_consolation", "tournament__city", "tournament")
    search_fields = (
        "player1__user__first_name",
        "player1__user__last_name",
        "player2__user__first_name",
        "player2__user__last_name",
    )
    raw_id_fields = ("player1", "player2", "team1", "team2", "winner", "winner_team", "court", "next_match")
    date_hierarchy = "scheduled_datetime"

    fieldsets = (
        (
            "Турнир",
            {"fields": ("tournament", "court", "round_name", "round_index", "round_order", "is_consolation", "next_match")},
        ),
        ("Игроки / Команды", {"fields": ("player1", "player2", "team1", "team2", "winner", "winner_team")}),
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
        ("Время", {"fields": ("scheduled_datetime", "deadline", "completed_datetime", "status")}),
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
    """Admin for match proposals. При смене статуса на «Подтверждено» результат автоматически применяется к матчу."""

    list_display = ("match", "proposer", "result", "status", "created_at")
    list_filter = ("status", "result")
    search_fields = ("match__tournament__name", "proposer__user__email")
    actions = [accept_proposal_action]


@admin.register(TournamentPlayerResult)
class TournamentPlayerResultAdmin(admin.ModelAdmin):
    """FAN: результаты игроков в турнире."""

    list_display = ("tournament", "player", "round_eliminated", "fan_points", "is_consolation")
    list_filter = ("tournament", "round_eliminated", "is_consolation")
    search_fields = (
        "player__user__first_name",
        "player__user__last_name",
        "tournament__name",
    )
    raw_id_fields = ("tournament", "player")
