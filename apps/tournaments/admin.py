"""
Tournaments admin configuration.
"""

from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.users.models import SkillLevel

from .fan import generate_bracket
from .olympic_consolation import generate_bracket as generate_olympic_bracket
from .round_robin import generate_bracket as generate_round_robin_bracket
from .proposal_service import apply_proposal
from .models import (
    DeadlineExtensionRequest,
    HeadToHead,
    Match,
    MatchResultProposal,
    SeasonRating,
    Tournament,
    TournamentAllowedCategory,
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


@admin.action(description="Сформировать сетку (олимпийская)")
def generate_olympic_bracket_action(modeladmin, request, queryset):
    for t in queryset:
        ok, msg = generate_olympic_bracket(t)
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
    classes = ("variant-doubles-only",)  # для JS: скрывать при варианте «Одиночный»


class TournamentAdminForm(forms.ModelForm):
    """Форма турнира с полем «Допустимые категории» в виде чекбоксов (1–5)."""

    allowed_categories = forms.MultipleChoiceField(
        choices=SkillLevel.choices,
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Допустимые категории участников",
        help_text="Отметьте от 1 до 5 категорий. Регистрироваться смогут только игроки с выбранными уровнями.",
    )

    class Meta:
        model = Tournament
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["allowed_categories"].initial = list(
                self.instance.allowed_categories.values_list("category", flat=True)
            )

    def clean_allowed_categories(self):
        value = self.cleaned_data.get("allowed_categories") or []
        if len(value) == 0:
            raise ValidationError("Выберите хотя бы одну категорию участников.")
        if len(value) > 5:
            raise ValidationError("Можно выбрать не более 5 категорий.")
        return value


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    form = TournamentAdminForm
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
        "min_participants",
        "max_participants",
        "min_teams",
        "max_teams",
    )
    list_filter = (
        "city",
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
    readonly_fields = ("insufficient_participants_notified_at",)
    actions = [generate_fan_bracket_action, generate_olympic_bracket_action, generate_round_robin_bracket_action]

    fieldsets = (
        ("Базовая информация", {"fields": ("name", "slug", "description", "image")}),
        ("Формат турнира", {"fields": ("format", "variant")}),
        (
            "Общие поля (FAN, Олимпийская, Круговой)",
            {
                "fields": (
                    "entry_fee",
                    "is_one_day",
                    "city",
                    "gender",
                    "allowed_categories",
                    "duration",
                    "tournament_type",
                    "status",
                    "start_date",
                    "end_date",
                    "registration_deadline",
                    "min_participants",
                    "max_participants",
                    "min_teams",
                    "max_teams",
                    "insufficient_participants_notified_at",
                    "bracket_generated",
                    "match_days_per_round",
                    "participants",
                ),
                "description": "Блок отображается после выбора формата турнира (FAN, Олимпийская система или Круговой).",
                "classes": ("format-common-section",),
            },
        ),
        (
            "FAN / Олимпийская: очки за раунд и места",
            {
                "fields": (
                    "fan_points_r1",
                    "fan_points_r2",
                    "fan_points_sf",
                    "fan_points_final",
                    "fan_points_winner",
                ),
                "description": "FAN: очки при вылете. Олимпийская система: очки по итоговому месту (1–2–3–4–5–8–9+).",
                "classes": ("format-fan-section", "format-olympic-section"),
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

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        selected = form.cleaned_data.get("allowed_categories") or []
        obj.allowed_categories.all().delete()
        for category in selected:
            TournamentAllowedCategory.objects.create(tournament=obj, category=category)

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
        css = {"all": ("css/admin_tournament.css",)}
        js = ("js/admin_tournament.js",)


class MatchAdminForm(forms.ModelForm):
    """Форма матча с понятными подписями для счёта по сетам."""

    class Meta:
        model = Match
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["player1_set1"].label = "Игрок 1 — 1‑й сет (геймы)"
        self.fields["player2_set1"].label = "Игрок 2 — 1‑й сет (геймы)"
        self.fields["player1_set2"].label = "Игрок 1 — 2‑й сет (геймы)"
        self.fields["player2_set2"].label = "Игрок 2 — 2‑й сет (геймы)"
        self.fields["player1_set3"].label = "Игрок 1 — 3‑й сет (геймы)"
        self.fields["player2_set3"].label = "Игрок 2 — 3‑й сет (геймы)"
        for i, name in enumerate(["player1_set1", "player2_set1", "player1_set2", "player2_set2", "player1_set3", "player2_set3"], 1):
            self.fields[name].help_text = "Количество выигранных геймов в сете. Игрок 1 и 2 — первая и вторая сторона в матче (см. выше)."


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    """Admin for Match model."""

    form = MatchAdminForm
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
    raw_id_fields = (
        "player1", "player2", "team1", "team2", "winner", "winner_team",
        "court", "next_match", "loser_next_match",
    )
    date_hierarchy = "scheduled_datetime"

    fieldsets = (
        (
            "Турнир",
            {
                "fields": (
                    "tournament", "court", "round_name", "round_index", "round_order",
                    "is_consolation", "next_match", "loser_next_match", "placement_min", "placement_max",
                )
            },
        ),
        ("Игроки / Команды", {"fields": ("player1", "player2", "team1", "team2", "winner", "winner_team")}),
        (
            "Счёт по сетам",
            {
                "fields": (
                    ("player1_set1", "player2_set1"),
                    ("player1_set2", "player2_set2"),
                    ("player1_set3", "player2_set3"),
                ),
                "description": "Игрок 1 и Игрок 2 — первая и вторая сторона в матче (см. блок выше). Укажите геймы в каждом сете (например 6 и 4 для счёта 6:4). Третий сет — только если играли тай-брейк или полный третий сет.",
            },
        ),
        ("Очки рейтинга", {"fields": ("points_player1", "points_player2")}),
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


class MatchResultProposalAdminForm(forms.ModelForm):
    """Форма предложения результата с понятными подписями для счёта."""

    class Meta:
        model = MatchResultProposal
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["player1_set1"].label = "Игрок 1 — 1‑й сет (геймы)"
        self.fields["player2_set1"].label = "Игрок 2 — 1‑й сет (геймы)"
        self.fields["player1_set2"].label = "Игрок 1 — 2‑й сет (геймы)"
        self.fields["player2_set2"].label = "Игрок 2 — 2‑й сет (геймы)"
        self.fields["player1_set3"].label = "Игрок 1 — 3‑й сет (геймы)"
        self.fields["player2_set3"].label = "Игрок 2 — 3‑й сет (геймы)"


@admin.register(MatchResultProposal)
class MatchResultProposalAdmin(admin.ModelAdmin):
    """Admin for match proposals. При смене статуса на «Подтверждено» результат автоматически применяется к матчу."""

    form = MatchResultProposalAdminForm
    list_display = ("match", "proposer", "result", "status", "created_at")
    list_filter = ("status", "result")
    search_fields = ("match__tournament__name", "proposer__user__email")
    actions = [accept_proposal_action]
    raw_id_fields = ("match", "proposer")

    fieldsets = (
        (None, {"fields": ("match", "proposer", "result", "status")}),
        (
            "Предложенный счёт по сетам",
            {
                "fields": (
                    ("player1_set1", "player2_set1"),
                    ("player1_set2", "player2_set2"),
                    ("player1_set3", "player2_set3"),
                ),
                "description": "Игрок 1 и Игрок 2 — первая и вторая сторона в матче. Геймы в 1‑м, 2‑м и 3‑м сете.",
            },
        ),
    )


@admin.action(description="Одобрить (+24 ч)")
def approve_extension_action(modeladmin, request, queryset):
    """Продлить дедлайн матча на 24 часа и отметить запрос как одобренный."""
    now = timezone.now()
    count = 0
    for ext in queryset.filter(status=DeadlineExtensionRequest.Status.PENDING):
        match = ext.match
        if match.status not in (Match.MatchStatus.SCHEDULED,):
            continue
        if match.deadline:
            match.deadline = match.deadline + timedelta(hours=24)
        else:
            match.deadline = now + timedelta(hours=24)
        match.save(update_fields=["deadline"])
        ext.status = DeadlineExtensionRequest.Status.APPROVED
        ext.processed_at = now
        ext.save(update_fields=["status", "processed_at"])
        try:
            from apps.telegram_bot import notifications as tg
            tg.notify_extension_approved(ext)
        except Exception:
            pass
        count += 1
    if count:
        messages.success(request, f"Одобрено запросов: {count}. Дедлайн продлён на 24 ч.")
    else:
        messages.warning(request, "Нет запросов для одобрения (или матчи уже завершены).")


@admin.register(DeadlineExtensionRequest)
class DeadlineExtensionRequestAdmin(admin.ModelAdmin):
    """Запросы на продление дедлайна матча (из кнопки в Telegram-боте)."""

    list_display = ("match", "requested_by", "status", "created_at", "processed_at")
    list_filter = ("status",)
    search_fields = ("match__tournament__name", "requested_by__user__email")
    actions = [approve_extension_action]
    raw_id_fields = ("match", "requested_by")
    readonly_fields = ("created_at",)


@admin.register(TournamentPlayerResult)
class TournamentPlayerResultAdmin(admin.ModelAdmin):
    """FAN / Олимпийская: результаты игроков в турнире (раунд вылета или итоговое место)."""

    list_display = ("tournament", "player", "place", "round_eliminated", "fan_points", "is_consolation")
    list_filter = ("tournament", "round_eliminated", "is_consolation")
    search_fields = (
        "player__user__first_name",
        "player__user__last_name",
        "tournament__name",
    )
    raw_id_fields = ("tournament", "player")
