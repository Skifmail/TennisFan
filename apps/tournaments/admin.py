"""
Tournaments admin configuration.
"""

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from apps.users.models import SkillLevel

from .fan import generate_bracket
from .models import (
    HeadToHead,
    Match,
    MatchResultProposal,
    SeasonArchive,
    SeasonPoints,
    SeasonRating,
    Tournament,
    TournamentAllowedCategory,
    TournamentEntryRefundRequest,
    TournamentPlayerResult,
    TournamentTeam,
    TVDGroup,
    TVDGroupMember,
    TVDTournament,
)
from .olympic_consolation import generate_bracket as generate_olympic_bracket
from .proposal_service import apply_proposal
from .round_robin import generate_bracket as generate_round_robin_bracket
from .tvd import (
    _assign_tvd_places_5_onwards,
)
from .tvd import (
    check_and_finalize as tvd_check_and_finalize,
)
from .tvd import (
    generate_groups as tvd_generate_groups,
)
from .tvd import (
    generate_playoffs as tvd_generate_playoffs,
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
        messages.warning(
            request, "Нет заявок для подтверждения (или матчи уже завершены)."
        )


@admin.action(description="Сформировать сетку (одноэтапная)")
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


@admin.action(description="Сформировать группы (ТВД)")
def generate_tvd_groups_action(modeladmin, request, queryset):
    for t in queryset:
        ok, msg = tvd_generate_groups(t)
        if ok:
            messages.success(request, f"{t.name}: {msg}")
        else:
            messages.warning(request, f"{t.name}: {msg}")


@admin.action(description="Сформировать плей-офф (ТВД)")
def generate_tvd_playoffs_action(modeladmin, request, queryset):
    for t in queryset:
        ok, msg = tvd_generate_playoffs(t)
        if ok:
            messages.success(request, f"{t.name}: {msg}")
        else:
            messages.warning(request, f"{t.name}: {msg}")


@admin.action(description="Завершить турнир (ТВД)")
def finalize_tvd_action(modeladmin, request, queryset):
    for t in queryset:
        ok, msg = tvd_check_and_finalize(t)
        if ok:
            messages.success(request, f"{t.name}: {msg}")
        else:
            messages.warning(request, f"{t.name}: {msg}")


@admin.action(description="Дозаполнить итоги ТВД (места 5+)")
def backfill_tvd_standings_action(modeladmin, request, queryset):
    """Для уже завершённых ТВД: присвоить места 5–8 и 9+ всем участникам без результата."""
    from .models import TournamentStatus

    for t in queryset.filter(status=TournamentStatus.COMPLETED):
        _assign_tvd_places_5_onwards(t)
        messages.success(request, f"{t.name}: итоги дозаполнены (места 5+).")


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

    def clean(self):
        cleaned_data = super().clean()
        variant = cleaned_data.get("variant")
        gender = cleaned_data.get("gender")
        # "Микст" доступен только для парных турниров
        if variant == "singles" and gender == "mixed":
            raise ValidationError(
                "Категория «Микст» доступна только для парных турниров. "
                "Для одиночных используйте «Смешанный» (любой пол)."
            )
        # Многодневный турнир: обязателен вступительный взнос (для регистрации по подписке или по оплате)
        is_one_day = cleaned_data.get("is_one_day")
        entry_fee = cleaned_data.get("entry_fee")
        if is_one_day is False and (entry_fee is None or float(entry_fee or 0) <= 0):
            raise ValidationError(
                "Для многодневного турнира укажите сумму вступительного взноса (руб). "
                "По подписке игроки регистрируются бесплатно в рамках лимита; без подписки или при исчерпанном лимите — оплата взноса."
            )
        return cleaned_data


@admin.register(TournamentEntryRefundRequest)
class TournamentEntryRefundRequestAdmin(admin.ModelAdmin):
    """Заявки на возврат взноса (участник удалён с турнира, взнос был оплачен)."""

    list_display = ("refund_ref", "tournament", "user", "amount", "removed_at")
    list_filter = ("tournament", "removed_at")
    search_fields = ("refund_ref", "user__email", "user__last_name", "user__first_name")
    readonly_fields = ("tournament", "user", "removed_at", "amount", "refund_ref")
    date_hierarchy = "removed_at"


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    form = TournamentAdminForm
    inlines = [TournamentTeamInline]

    """Admin for Tournament model.

    Страница добавления турнира: базовая информация + выбор формата.
    Поля формата появляются динамически при выборе (одноэтапная сетка, олимпийская, круговой).
    """

    list_display = (
        "name",
        "city",
        "court",
        "format",
        "variant",
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
    actions = [
        generate_fan_bracket_action,
        generate_olympic_bracket_action,
        generate_round_robin_bracket_action,
    ]

    fieldsets = (
        ("Базовая информация", {"fields": ("name", "slug", "description", "image")}),
        ("Формат турнира", {"fields": ("format", "variant")}),
        (
            "Общие поля (одноэтапная сетка, Олимпийская, Круговой)",
            {
                "fields": (
                    "entry_fee",
                    "is_one_day",
                    "city",
                    "court",
                    "gender",
                    "allowed_categories",
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
                "description": "Блок отображается после выбора формата турнира (Одноэтапная сетка, Олимпийская система или Круговой).",
                "classes": ("format-common-section",),
            },
        ),
        (
            "Одноэтапная сетка / Олимпийская / Круговой: очки за раунды и места",
            {
                "fields": (
                    "fan_points_r1",
                    "fan_points_r2",
                    "fan_points_sf",
                    "fan_points_final",
                    "fan_points_winner",
                ),
                "description": (
                    "<b>Одноэтапная сетка:</b> очки начисляются при вылете из раунда. "
                    "<b>Олимпийская система:</b> очки по итоговому месту (1–2–3–4–5–8–9+). "
                    "<b>Круговой:</b> очки в общий рейтинг начисляются по итоговому месту после завершения турнира "
                    "(1 место = победитель, 2 = финалист, 3–4 = полуфинал, 5–8 = 2 круг, 9+ = 1 круг). "
                    "Внутри кругового турнира места определяются по победам (1 очко за победу, 0 за поражение), эти значения не редактируются.\n\n"
                    "<b>Значения по умолчанию:</b> 10–25–45–70–100."
                ),
                "classes": (
                    "format-fan-section",
                    "format-olympic-section",
                    "format-round-robin-section",
                ),
            },
        ),
        (
            "Круговой: формат матча",
            {
                "fields": ("match_format",),
                "classes": ("format-round-robin-section",),
                "description": (
                    "Формат матча влияет на тай-брейки и подсчёт очков в таблице. "
                    "Очки 1 за победу и 0 за поражение используются только для определения мест внутри турнира и не настраиваются."
                ),
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
                resolver_match
                and "add" in (getattr(resolver_match, "url_name", "") or "")
            )
            if is_add_page:
                kwargs["choices"] = [("", "---------")] + list(db_field.choices)
                kwargs["initial"] = ""
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    class Media:
        css = {"all": ("css/admin_tournament.css",)}
        js = ("js/admin_tournament.js",)


class TVDGroupMemberInline(admin.TabularInline):
    model = TVDGroupMember
    extra = 0
    raw_id_fields = ("player",)
    ordering = ("final_place", "seed")


class TVDGroupInline(admin.TabularInline):
    model = TVDGroup
    extra = 0
    ordering = ("order",)
    show_change_link = True


class TVDTournamentAdminForm(TournamentAdminForm):
    """Форма ТВД: format и is_one_day выставляются автоматически, добавлено поле «Бесплатный»."""

    is_free = forms.BooleanField(
        required=False,
        initial=False,
        label="Бесплатный",
        help_text="Если отмечено — взнос 0 ₽, регистрироваться могут все желающие (с учётом категорий). Иначе обязательно укажите сумму взноса.",
    )

    class Meta(TournamentAdminForm.Meta):
        exclude = ("format", "is_one_day")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and (self.instance.entry_fee or 0) == 0:
            self.fields["is_free"].initial = True

    def clean(self):
        cleaned_data = super().clean()
        is_free = cleaned_data.get("is_free")
        entry_fee = cleaned_data.get("entry_fee")
        if is_free:
            cleaned_data["entry_fee"] = 0
        elif entry_fee is None or (entry_fee is not None and float(entry_fee) <= 0):
            raise ValidationError(
                "Укажите сумму взноса (руб) или отметьте «Бесплатный»."
            )
        return cleaned_data


@admin.register(TVDTournament)
class TVDTournamentAdmin(admin.ModelAdmin):
    """Админка для турниров выходного дня (proxy над Tournament, format=weekend_day)."""

    form = TVDTournamentAdminForm
    inlines = [TVDGroupInline]

    list_display = (
        "name",
        "city",
        "variant",
        "status",
        "bracket_generated",
        "start_date",
        "max_participants",
    )
    list_filter = ("city", "status", "bracket_generated")
    search_fields = ("name", "description")
    list_editable = ("status",)
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("participants",)
    date_hierarchy = "start_date"
    readonly_fields = ("insufficient_participants_notified_at",)
    actions = [
        generate_tvd_groups_action,
        generate_tvd_playoffs_action,
        finalize_tvd_action,
        backfill_tvd_standings_action,
    ]

    fieldsets = (
        ("Базовая информация", {"fields": ("name", "slug", "description", "image")}),
        (
            "ТВД (формат и участники)",
            {
                "fields": (
                    "variant",
                    "is_free",
                    "entry_fee",
                    "city",
                    "court",
                    "gender",
                    "allowed_categories",
                    "tournament_type",
                    "status",
                    "start_date",
                    "end_date",
                    "registration_deadline",
                    "min_participants",
                    "max_participants",
                    "insufficient_participants_notified_at",
                    "bracket_generated",
                    "match_days_per_round",
                    "participants",
                ),
            },
        ),
        (
            "Очки за места",
            {
                "fields": (
                    "fan_points_r1",
                    "fan_points_r2",
                    "fan_points_sf",
                    "fan_points_final",
                    "fan_points_winner",
                ),
            },
        ),
        ("Формат матча в группах", {"fields": ("match_format",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(format="weekend_day")

    def save_model(self, request, obj, form, change):
        from .models import TournamentDuration, TournamentFormat

        obj.format = TournamentFormat.WEEKEND_DAY
        obj.is_one_day = True  # ТВД всегда однодневный
        obj.duration = TournamentDuration.SINGLE_DAY
        if form.cleaned_data.get("is_free"):
            obj.entry_fee = 0
        super().save_model(request, obj, form, change)
        selected = form.cleaned_data.get("allowed_categories") or []
        obj.allowed_categories.all().delete()
        for category in selected:
            TournamentAllowedCategory.objects.create(tournament=obj, category=category)


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
        for _i, name in enumerate(
            [
                "player1_set1",
                "player2_set1",
                "player1_set2",
                "player2_set2",
                "player1_set3",
                "player2_set3",
            ],
            1,
        ):
            self.fields[name].help_text = (
                "Количество выигранных геймов в сете. Игрок 1 и 2 — первая и вторая сторона в матче (см. выше)."
            )


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
        "player1",
        "player2",
        "team1",
        "team2",
        "winner",
        "winner_team",
        "court",
        "next_match",
        "loser_next_match",
        "tvd_group",
    )
    date_hierarchy = "scheduled_datetime"

    fieldsets = (
        (
            "Турнир",
            {
                "fields": (
                    "tournament",
                    "court",
                    "round_name",
                    "round_index",
                    "round_order",
                    "is_consolation",
                    "tvd_group",
                    "tvd_stage",
                    "next_match",
                    "loser_next_match",
                    "placement_min",
                    "placement_max",
                )
            },
        ),
        (
            "Игроки / Команды",
            {
                "fields": (
                    "player1",
                    "player2",
                    "team1",
                    "team2",
                    "winner",
                    "winner_team",
                )
            },
        ),
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
        (
            "Время",
            {
                "fields": (
                    "scheduled_datetime",
                    "deadline",
                    "completed_datetime",
                    "status",
                )
            },
        ),
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


@admin.register(TournamentPlayerResult)
class TournamentPlayerResultAdmin(admin.ModelAdmin):
    """Результаты игроков в турнире (раунд вылета или итоговое место)."""

    list_display = (
        "tournament",
        "player",
        "place",
        "round_eliminated",
        "fan_points",
        "is_consolation",
    )


@admin.register(TVDGroup)
class TVDGroupAdmin(admin.ModelAdmin):
    """Группы ТВД. Участников можно редактировать во вкладке ниже."""

    list_display = ("tournament", "name", "order", "is_completed")
    list_filter = ("tournament", "is_completed")
    ordering = ("tournament", "order")
    inlines = [TVDGroupMemberInline]
    raw_id_fields = ("tournament",)


@admin.register(TVDGroupMember)
class TVDGroupMemberAdmin(admin.ModelAdmin):
    """Участник группы ТВД (ручное редактирование мест и статистики)."""

    list_display = (
        "group",
        "player",
        "seed",
        "wins",
        "losses",
        "games_won",
        "games_lost",
        "final_place",
    )
    list_filter = ("group__tournament",)
    search_fields = ("player__user__first_name", "player__user__last_name")
    raw_id_fields = ("group", "player")


@admin.register(SeasonPoints)
class SeasonPointsAdmin(admin.ModelAdmin):
    """Админка для сезонных очков игроков."""

    list_display = (
        "player",
        "current_season_points",
        "season_name",
        "season_year",
        "updated_at",
    )
    list_filter = ("season_name", "season_year")
    search_fields = (
        "player__user__first_name",
        "player__user__last_name",
        "player__user__email",
    )
    readonly_fields = ("updated_at",)
    ordering = ("-current_season_points", "-season_year", "-season_name")


@admin.register(SeasonArchive)
class SeasonArchiveAdmin(admin.ModelAdmin):
    """Админка для архива результатов сезонов."""

    list_display = (
        "player",
        "season_name",
        "season_year",
        "final_points",
        "final_rank",
        "archived_at",
    )
    list_filter = ("season_name", "season_year", "final_rank")
    search_fields = (
        "player__user__first_name",
        "player__user__last_name",
        "player__user__email",
    )
    readonly_fields = ("archived_at",)
    raw_id_fields = ("player",)
    ordering = ("-season_year", "-season_name", "final_rank", "-final_points")
