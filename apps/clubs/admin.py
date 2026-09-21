"""
Регистрация моделей клубного раздела в Django Admin.

Расширенная конфигурация для platform_admin: инлайны, действия (блокировка, смена тарифа),
финансовая сводка, аудит-лог, глобальные настройки платформы.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.autocomplete import AutocompleteJsonView
from django.contrib.admin.widgets import AutocompleteSelect
from django.db.models import Count, Q, QuerySet, Sum
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html_join
from django.utils.safestring import mark_safe

from apps.tournaments.admin import TournamentAdmin
from apps.tournaments.platform_home import order_with_cancelled_last
from apps.users.display import format_user_admin_label
from apps.users.models import User

from .models import (
    Club,
    ClubFeePayment,
    ClubInviteLink,
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubLegalDocument,
    ClubMember,
    ClubMemberPlan,
    ClubMemberRole,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubNotificationConfig,
    ClubNotificationSettings,
    ClubPlanSlotUsage,
    ClubPlanTournamentAccess,
    ClubPlayerPlan,
    ClubRating,
    ClubRatingHistory,
    ClubStatus,
    ClubSubscription,
    ClubSubscriptionStatus,
    ClubTournament,
    ClubTournamentApplication,
    PlatformAuditLog,
    PlatformPlan,
    PlatformSettings,
)
from .services import (
    approve_club_join_request,
    assign_club_administrator,
    log_platform_action,
    reject_club_join_request,
)

# ---------------------------------------------------------------------------
# Search helpers (регистронезависимый поиск по кириллице + телефоны)
# ---------------------------------------------------------------------------


def _case_variants_for_cyrillic(value: str) -> list[str]:
    """
    Сгенерировать варианты регистра для поиска по кириллице.

    Args:
        value: Токен запроса.

    Returns:
        Список вариантов регистра, подходящих для `__contains`.
    """

    variants = [value, value.lower(), value.upper(), value.title()]
    return list(dict.fromkeys(v for v in variants if v))


def _phone_variants_for_search(token: str) -> list[str]:
    """
    Подготовить варианты телефона для поиска по `User.phone`.

    Поддерживаются вводы:
    - `+79381138222`
    - `79381138222`
    - `9381138222`

    Args:
        token: Токен запроса.

    Returns:
        Список вариантов для `__contains` по нормализованным строкам.
    """

    digits = "".join(ch for ch in token if ch.isdigit())
    if not digits:
        return []

    normalized_digits: str | None = None
    if len(digits) == 11:
        if digits.startswith("8"):
            normalized_digits = "7" + digits[1:]
        elif digits.startswith("7"):
            normalized_digits = digits
    elif len(digits) == 10:
        normalized_digits = "7" + digits

    if not normalized_digits:
        return []

    alt8_digits = "8" + normalized_digits[1:]
    variants = [
        f"+{normalized_digits}",
        normalized_digits,
        f"+{alt8_digits}",
        alt8_digits,
    ]
    return list(dict.fromkeys(variants))


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


class ClubSubscriptionInline(admin.TabularInline):
    """Подписки клуба (inline в ClubAdmin)."""

    model = ClubSubscription
    extra = 0
    fields = ("plan", "period", "price", "started_at", "ends_at", "status")
    readonly_fields = ("started_at",)
    show_change_link = True


class ClubMemberInline(admin.TabularInline):
    """Участники клуба (inline, только для чтения)."""

    model = ClubMember
    extra = 0
    fields = ("user", "role", "status", "joined_at")
    readonly_fields = ("user", "role", "status", "joined_at")
    show_change_link = True

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


class ClubMembershipFeeInline(admin.TabularInline):
    """Настройки взносов (inline)."""

    model = ClubMembershipFee
    extra = 0
    fields = ("amount", "currency", "period", "is_active")
    show_change_link = True


class ClubNotificationConfigInline(admin.StackedInline):
    """Настройки уведомлений клуба (inline)."""

    model = ClubNotificationConfig
    extra = 0
    can_delete = False


class ClubLegalDocumentInline(admin.StackedInline):
    """Оферта клуба (одна запись на клуб)."""

    model = ClubLegalDocument
    extra = 0
    max_num = 1
    can_delete = False
    fields = ("title", "content", "version", "is_published", "updated_at")
    readonly_fields = ("updated_at",)


# ---------------------------------------------------------------------------
# Фильтры
# ---------------------------------------------------------------------------


class CurrentPlanFilter(admin.SimpleListFilter):
    """Фильтр по тарифу текущей активной подписки клуба."""

    title = "Текущий тариф"
    parameter_name = "current_plan"

    def lookups(self, request: HttpRequest, model_admin):
        return [
            ("start", "Старт"),
            ("basic", "Базовый"),
            ("pro", "Про"),
            ("none", "Без активной подписки"),
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet):
        value = self.value()
        if not value:
            return queryset
        if value == "none":
            return queryset.exclude(
                subscriptions__status=ClubSubscriptionStatus.ACTIVE,
            )
        return queryset.filter(
            subscriptions__status=ClubSubscriptionStatus.ACTIVE,
            subscriptions__plan=value,
        ).distinct()


# ---------------------------------------------------------------------------
# Club Admin
# ---------------------------------------------------------------------------


class NamedUserAutocompleteSelect(AutocompleteSelect):
    """Autocomplete пользователей с отдельным URL, где в подписи есть имя."""

    url_name = "%s:clubs_named_user_autocomplete"


class NamedUserAutocompleteJsonView(AutocompleteJsonView):
    """JSON для выбора пользователя: «Имя Фамилия — email»."""

    def serialize_result(self, obj: User, to_field_name: str) -> dict[str, str]:
        """Вернуть id и подпись пользователя для Select2."""
        return {
            "id": str(getattr(obj, to_field_name)),
            "text": format_user_admin_label(obj),
        }


class NamedUserChoiceField(forms.ModelChoiceField):
    """Поле выбора пользователя с именем и email в подписи."""

    def label_from_instance(self, obj: User) -> str:
        """Подпись выбранного пользователя."""
        return format_user_admin_label(obj)


class ClubAdminForm(forms.ModelForm):
    """Форма клуба с полем назначения администратора."""

    new_admin = NamedUserChoiceField(
        label="Добавить администратора",
        queryset=User.objects.filter(is_active=True).order_by("email"),
        required=False,
        help_text=(
            "Выберите пользователя платформы. Он станет администратором этого клуба. "
            "Если пользователь уже участник — его роль будет повышена."
        ),
        widget=NamedUserAutocompleteSelect(
            ClubMember._meta.get_field("user"),
            admin.site,
        ),
    )

    class Meta:
        model = Club
        fields = "__all__"


@admin.register(Club)
class ClubAdmin(admin.ModelAdmin):
    """Расширенная админка клуба для platform_admin."""

    form = ClubAdminForm
    change_list_template = "admin/clubs/club_changelist.html"

    class Media:
        """Подключить автодополнение населённых пунктов на форме клуба."""

        js = ("js/city_autocomplete.js",)

    list_display = (
        "name",
        "slug",
        "city",
        "status",
        "current_plan_display",
        "members_count_display",
        "is_public",
        "created_at",
    )
    list_filter = ("status", "is_public", CurrentPlanFilter, "city")
    search_fields = ("name", "slug", "email", "admin_name")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = (
        "created_at",
        "members_count_display",
        "tournaments_count_display",
        "current_admins_display",
    )
    inlines = [
        ClubSubscriptionInline,
        ClubMemberInline,
        ClubMembershipFeeInline,
        ClubNotificationConfigInline,
        ClubLegalDocumentInline,
    ]
    actions = ["block_clubs", "unblock_clubs", "reset_trial"]

    def get_fieldsets(
        self,
        request: HttpRequest,
        obj: Club | None = None,
    ) -> list[tuple[str | None, dict[str, Any]]]:
        """Вынести назначение администратора в отдельный блок в начале формы."""
        fieldsets = list(super().get_fieldsets(request, obj))
        heading, options = fieldsets[0]
        fields = tuple(
            field
            for field in options.get("fields", ())
            if field not in {"new_admin", "current_admins_display"}
        )
        fieldsets[0] = (heading, {**options, "fields": fields})
        return [
            (
                "Администратор клуба",
                {
                    "fields": ("current_admins_display", "new_admin"),
                    "description": (
                        "Текущие администраторы клуба. Чтобы добавить ещё одного, "
                        "выберите пользователя ниже и сохраните клуб."
                    ),
                },
            ),
            *fieldsets,
        ]

    def save_model(
        self,
        request: HttpRequest,
        obj: Club,
        form: forms.ModelForm,
        change: bool,
    ) -> None:
        """Сохранить клуб и при необходимости назначить администратора."""
        super().save_model(request, obj, form, change)
        new_admin = form.cleaned_data.get("new_admin")
        if not new_admin:
            return
        _member, kind = assign_club_administrator(
            obj,
            new_admin,
            reviewed_by=request.user,
        )
        if kind == "already_admin":
            self.message_user(
                request,
                f"Пользователь {new_admin.email} уже является администратором клуба.",
                messages.INFO,
            )
            return
        log_platform_action(
            actor=request.user,
            action="club_admin_assigned",
            club=obj,
            details=f"Администратор: {new_admin.email} (id={new_admin.pk})",
        )
        if kind == "created":
            self.message_user(
                request,
                f"Пользователь {new_admin.email} назначен администратором клуба.",
                messages.SUCCESS,
            )
            return
        self.message_user(
            request,
            (
                f"Пользователь {new_admin.email} уже был участником "
                "и повышен до администратора."
            ),
            messages.SUCCESS,
        )

    def get_urls(self):
        custom_urls = [
            path(
                "named-user-autocomplete/",
                self.admin_site.admin_view(
                    NamedUserAutocompleteJsonView.as_view(admin_site=self.admin_site)
                ),
                name="clubs_named_user_autocomplete",
            ),
            path(
                "financial-summary/",
                self.admin_site.admin_view(self.financial_summary_view),
                name="clubs_financial_summary",
            ),
        ]
        return custom_urls + super().get_urls()

    def financial_summary_view(self, request: HttpRequest) -> HttpResponse:
        """Финансовая сводка по подпискам клубов."""
        now = timezone.now()
        active_subs = ClubSubscription.objects.filter(
            status=ClubSubscriptionStatus.ACTIVE
        )

        plan_stats = {}
        for plan_value, plan_label in [
            ("start", "Старт"),
            ("basic", "Базовый"),
            ("pro", "Про"),
        ]:
            plan_qs = active_subs.filter(plan=plan_value)
            plan_stats[plan_label] = plan_qs.count()

        total_active = active_subs.count()

        mrr = Decimal("0.00")
        for sub in active_subs:
            if sub.period == "monthly":
                mrr += sub.price
            elif sub.period == "yearly":
                mrr += sub.price / Decimal("12")

        expiring_7 = active_subs.filter(
            ends_at__lte=now + timedelta(days=7),
            ends_at__gt=now,
        ).count()
        expiring_30 = active_subs.filter(
            ends_at__lte=now + timedelta(days=30),
            ends_at__gt=now,
        ).count()

        suspended_count = Club.objects.filter(status=ClubStatus.SUSPENDED).count()

        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_revenue = ClubSubscription.objects.filter(
            started_at__gte=month_start,
        ).aggregate(total=Sum("price"))["total"] or Decimal("0.00")

        context = {
            **self.admin_site.each_context(request),
            "title": "Финансовая сводка по подпискам",
            "plan_stats": plan_stats,
            "total_active": total_active,
            "mrr": mrr,
            "expiring_7": expiring_7,
            "expiring_30": expiring_30,
            "suspended_count": suspended_count,
            "month_revenue": month_revenue,
        }
        return TemplateResponse(request, "admin/clubs/financial_summary.html", context)

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return (
            super()
            .get_queryset(request)
            .annotate(
                _members_count=Count("members", distinct=True),
                _tournaments_count=Count("tournaments", distinct=True),
            )
        )

    @admin.display(description="Участников", ordering="_members_count")
    def members_count_display(self, obj: Club) -> int:
        return getattr(obj, "_members_count", 0)

    @admin.display(description="Турниров", ordering="_tournaments_count")
    def tournaments_count_display(self, obj: Club) -> int:
        return getattr(obj, "_tournaments_count", 0)

    @admin.display(description="Текущие администраторы")
    def current_admins_display(self, obj: Club) -> str:
        """Показать активных администраторов клуба с именем и email."""
        if not obj.pk:
            return "—"
        admins = (
            obj.members.filter(
                role=ClubMemberRole.ADMIN,
                status=ClubMemberStatus.ACTIVE,
            )
            .select_related("user")
            .order_by("user__last_name", "user__first_name", "user__email")
        )
        items = [
            (
                reverse("admin:users_user_change", args=[member.user.pk]),
                format_user_admin_label(member.user),
            )
            for member in admins
        ]
        if not items:
            return "Не назначены"
        return cast(
            str,
            format_html_join(mark_safe("<br>"), '<a href="{}">{}</a>', items),
        )

    @admin.display(description="Тариф")
    def current_plan_display(self, obj: Club) -> str:
        sub = (
            obj.subscriptions.filter(status=ClubSubscriptionStatus.ACTIVE)
            .order_by("-ends_at")
            .first()
        )
        if sub:
            return str(sub.get_plan_display())
        return "—"

    @admin.action(description="Заблокировать выбранные клубы")
    def block_clubs(self, request: HttpRequest, queryset: QuerySet) -> None:
        updated = 0
        for club in queryset.exclude(status=ClubStatus.SUSPENDED):
            club.status = ClubStatus.SUSPENDED
            club.save(update_fields=["status"])
            log_platform_action(
                actor=request.user,
                action="club_blocked",
                club=club,
                details="Заблокирован через Django Admin",
            )
            updated += 1
        self.message_user(
            request, f"Заблокировано клубов: {updated}.", messages.SUCCESS
        )

    @admin.action(description="Разблокировать выбранные клубы")
    def unblock_clubs(self, request: HttpRequest, queryset: QuerySet) -> None:
        updated = 0
        for club in queryset.filter(status=ClubStatus.SUSPENDED):
            club.status = ClubStatus.ACTIVE
            club.save(update_fields=["status"])
            log_platform_action(
                actor=request.user,
                action="club_unblocked",
                club=club,
                details="Разблокирован через Django Admin",
            )
            updated += 1
        self.message_user(
            request, f"Разблокировано клубов: {updated}.", messages.SUCCESS
        )

    @admin.action(description="Сбросить trial (14 дней от сейчас)")
    def reset_trial(self, request: HttpRequest, queryset: QuerySet) -> None:
        now = timezone.now()
        ps = PlatformSettings.load()
        trial_ends = now + timedelta(days=ps.trial_days)
        updated = 0
        for club in queryset:
            club.status = ClubStatus.TRIAL
            club.trial_ends_at = trial_ends
            club.save(update_fields=["status", "trial_ends_at"])
            log_platform_action(
                actor=request.user,
                action="trial_reset",
                club=club,
                details=f"Trial сброшен до {trial_ends:%d.%m.%Y}",
            )
            updated += 1
        self.message_user(
            request, f"Trial сброшен для {updated} клуба(ов).", messages.SUCCESS
        )


# ---------------------------------------------------------------------------
# Другие модели
# ---------------------------------------------------------------------------


@admin.register(ClubSubscription)
class ClubSubscriptionAdmin(admin.ModelAdmin):
    """Админка подписки клуба."""

    list_display = (
        "club",
        "plan",
        "period",
        "price",
        "started_at",
        "ends_at",
        "status",
    )
    list_filter = ("plan", "period", "status")
    search_fields = ("club__name",)
    readonly_fields = ("started_at",)


@admin.register(ClubMember)
class ClubMemberAdmin(admin.ModelAdmin):
    """Админка участника клуба."""

    list_display = (
        "user_email",
        "user_last_name",
        "user_first_name",
        "club",
        "role",
        "status",
        "joined_at",
        "created_at",
    )
    list_filter = ("role", "status", "club")
    search_fields = (
        "user__email",
        "user__last_name",
        "user__first_name",
        "user__phone",
        "club__name",
    )
    autocomplete_fields = ("user",)
    raw_id_fields = ("invited_by",)
    readonly_fields = ("created_at",)

    @admin.display(description="Email", ordering="user__email")
    def user_email(self, obj: ClubMember) -> str:
        """Вернуть email пользователя участника клуба.

        Args:
            obj: Запись `ClubMember`.

        Returns:
            Email пользователя.
        """

        return cast(str, obj.user.email)

    @admin.display(description="Фамилия", ordering="user__last_name")
    def user_last_name(self, obj: ClubMember) -> str:
        """Вернуть фамилию пользователя участника клуба.

        Args:
            obj: Запись `ClubMember`.

        Returns:
            Фамилия пользователя.
        """

        return cast(str, obj.user.last_name)

    @admin.display(description="Имя", ordering="user__first_name")
    def user_first_name(self, obj: ClubMember) -> str:
        """Вернуть имя пользователя участника клуба.

        Args:
            obj: Запись `ClubMember`.

        Returns:
            Имя пользователя.
        """

        return cast(str, obj.user.first_name)

    def get_search_results(
        self,
        request: HttpRequest,
        queryset: QuerySet[ClubMember],
        search_term: str,
    ) -> tuple[QuerySet[ClubMember], bool]:
        """
        Выполнить поиск по email/ФИО/телефону и названию клуба.

        Поиск по ФИО и названию клуба устойчив к регистру кириллицы,
        так как делаем поиск через `__contains` по нескольким вариантам
        регистра (`lower/upper/title`), избегая проблемных преобразований
        вроде `LOWER()` на SQLite.

        По телефону поддерживаются вводы:
        `+79381138222`, `79381138222`, `9381138222`.

        Args:
            request: Текущий HTTP-запрос админки.
            queryset: Базовый queryset для фильтрации.
            search_term: Строка поиска из формы админки.

        Returns:
            Кортеж (queryset, may_have_duplicates).
        """

        if not search_term:
            return queryset, False

        tokens = [t for t in search_term.split() if t]
        if not tokens:
            return queryset, False

        combined_q = Q()
        for token in tokens:
            email_q = Q(user__email__icontains=token)

            name_q = Q()
            club_name_q = Q()
            for variant in _case_variants_for_cyrillic(token):
                name_q |= Q(user__last_name__contains=variant)
                name_q |= Q(user__first_name__contains=variant)
                club_name_q |= Q(club__name__contains=variant)

            token_q = email_q | name_q | club_name_q

            phone_variants = _phone_variants_for_search(token)
            if phone_variants:
                phone_q = Q()
                for variant in phone_variants:
                    phone_q |= Q(user__phone__contains=variant)
                token_q |= phone_q

            combined_q &= token_q

        return queryset.filter(combined_q), False


@admin.register(ClubInviteLink)
class ClubInviteLinkAdmin(admin.ModelAdmin):
    """Админка инвайт-ссылки."""

    list_display = (
        "club",
        "token_short",
        "use_count",
        "max_uses",
        "is_active",
        "expires_at",
        "created_at",
    )
    list_filter = ("is_active", "club")
    search_fields = ("token", "club__name")
    raw_id_fields = ("created_by",)

    @admin.display(description="Токен")
    def token_short(self, obj: ClubInviteLink) -> str:
        return f"{obj.token[:12]}..." if obj.token else "—"


@admin.register(ClubJoinRequest)
class ClubJoinRequestAdmin(admin.ModelAdmin):
    """Админка заявок игроков на вступление в клуб."""

    list_display = (
        "user_display",
        "club",
        "status",
        "message",
        "created_at",
        "reviewed_by_display",
        "reviewed_at",
    )
    list_filter = ("status", "club")
    search_fields = (
        "user__email",
        "user__first_name",
        "user__last_name",
        "club__name",
        "message",
    )
    raw_id_fields = ("user", "club", "reviewed_by")
    readonly_fields = (
        "club",
        "user",
        "status",
        "message",
        "reviewed_by",
        "reviewed_at",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
    actions = ["approve_selected", "reject_selected"]
    ordering = ("-created_at",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Заявки создаются игроками на публичной странице клуба."""
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: ClubJoinRequest | None = None
    ) -> bool:
        """Удаление скрывает заявку без уведомления игрока."""
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet[ClubJoinRequest]:
        """Подгрузить игрока, клуб и рецензента для списка заявок."""
        return (
            super().get_queryset(request).select_related("user", "club", "reviewed_by")
        )

    def _review_selected(
        self,
        request: HttpRequest,
        queryset: QuerySet[ClubJoinRequest],
        *,
        approve: bool,
    ) -> None:
        """Одобрить или отклонить выбранные pending-заявки."""
        done = 0
        skipped = 0
        for join_request in queryset:
            if join_request.status != ClubJoinRequestStatus.PENDING:
                skipped += 1
                continue
            try:
                if approve:
                    dashboard_url = request.build_absolute_uri(
                        reverse(
                            "clubs:dashboard",
                            kwargs={"slug": join_request.club.slug},
                        )
                    )
                    approve_club_join_request(
                        join_request,
                        reviewed_by=request.user,
                        dashboard_url=dashboard_url,
                    )
                else:
                    reject_club_join_request(join_request, reviewed_by=request.user)
            except ValueError:
                skipped += 1
                continue
            done += 1
        verb = "Одобрено" if approve else "Отклонено"
        if done:
            self.message_user(request, f"{verb} заявок: {done}.")
        if skipped:
            self.message_user(
                request,
                f"Пропущено уже обработанных: {skipped}.",
                level=messages.WARNING,
            )

    @admin.display(description="Игрок", ordering="user__last_name")
    def user_display(self, obj: ClubJoinRequest) -> str:
        """Имя и email игрока."""
        return format_user_admin_label(obj.user)

    @admin.display(description="Кто обработал", ordering="reviewed_by__last_name")
    def reviewed_by_display(self, obj: ClubJoinRequest) -> str:
        """Имя обработавшего заявку или прочерк."""
        if obj.reviewed_by is None:
            return "—"
        return format_user_admin_label(obj.reviewed_by)

    @admin.action(description="Одобрить выбранные заявки")
    def approve_selected(
        self, request: HttpRequest, queryset: QuerySet[ClubJoinRequest]
    ) -> None:
        """Одобрить pending-заявки и добавить игроков в клубы."""
        self._review_selected(request, queryset, approve=True)

    @admin.action(description="Отклонить выбранные заявки")
    def reject_selected(
        self, request: HttpRequest, queryset: QuerySet[ClubJoinRequest]
    ) -> None:
        """Отклонить pending-заявки без добавления в клуб."""
        self._review_selected(request, queryset, approve=False)


@admin.register(ClubMembershipFee)
class ClubMembershipFeeAdmin(admin.ModelAdmin):
    """Админка настройки взносов клуба."""

    list_display = ("club", "amount", "currency", "period", "is_active", "created_at")
    list_filter = ("period", "is_active")
    search_fields = ("club__name",)
    readonly_fields = ("created_at",)


@admin.register(ClubFeePayment)
class ClubFeePaymentAdmin(admin.ModelAdmin):
    """Админка оплаты взноса."""

    list_display = ("member", "club", "period_label", "amount", "paid_at", "method")
    list_filter = ("method", "club")
    search_fields = ("member__user__email", "period_label")
    raw_id_fields = ("member", "marked_by")
    date_hierarchy = "paid_at"


@admin.register(ClubRating)
class ClubRatingAdmin(admin.ModelAdmin):
    """Админка рейтинга в клубе."""

    list_display = ("member", "club", "points", "rank", "updated_at")
    list_filter = ("club",)
    search_fields = ("member__user__email", "club__name")
    readonly_fields = ("updated_at",)


@admin.register(ClubRatingHistory)
class ClubRatingHistoryAdmin(admin.ModelAdmin):
    """Админка истории рейтинга."""

    list_display = (
        "club_rating",
        "tournament",
        "points_before",
        "points_after",
        "delta",
        "created_at",
    )
    list_filter = ("club_rating__club",)
    raw_id_fields = ("tournament",)
    readonly_fields = ("created_at",)


@admin.register(ClubTournament)
class ClubTournamentAdmin(TournamentAdmin):
    """
    Админка турниров с клубом-организатором.

    Записи с непустым ``club`` не попадают в разделы «Турниры → Многодневные/Однодневные»;
    управление ими сосредоточено здесь (приложение «Клубы»).
    """

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        """
        Ограничить список турнирами, у которых указан организатор-клуб.

        Args:
            request: HTTP-запрос админки.

        Returns:
            QuerySet с ``club__isnull=False`` и ``select_related`` для клуба и корта.
        """
        qs = admin.ModelAdmin.get_queryset(self, request)
        return order_with_cancelled_last(
            qs.filter(club__isnull=False).select_related("club", "court"),
            "-start_date",
        )

    def get_list_display(self, request: HttpRequest) -> tuple[str, ...]:
        """Добавить колонку «Клуб» после названия."""
        parent = super().get_list_display(request)
        if len(parent) < 2:
            return tuple(parent)
        return (parent[0], "club", parent[1], *parent[2:])

    def get_list_filter(self, request: HttpRequest) -> tuple[Any, ...]:
        """Фильтр по клубу первым в списке."""
        return ("club", *super().get_list_filter(request))

    def get_fieldsets(
        self, request: HttpRequest, obj: ClubTournament | None = None
    ) -> tuple[tuple[str, dict], ...]:
        """
        Дополняет первый fieldset полями ``club`` и ``is_open_interclub``.

        Args:
            request: HTTP-запрос админки.
            obj: Редактируемый турнир или None на странице создания.

        Returns:
            Кортеж fieldsets с полями клуба в блоке «Базовая информация».
        """
        fieldsets = list(super().get_fieldsets(request, obj))
        heading, options = fieldsets[0]
        fields = list(options["fields"])
        if "club" not in fields:
            slug_idx = fields.index("slug")
            fields.insert(slug_idx + 1, "club")
            fields.insert(slug_idx + 2, "is_open_interclub")
            fieldsets[0] = (heading, {**options, "fields": tuple(fields)})
        return tuple(fieldsets)


@admin.register(ClubTournamentApplication)
class ClubTournamentApplicationAdmin(admin.ModelAdmin):
    """Админка заявки клуба на турнир."""

    list_display = (
        "applicant_club",
        "tournament",
        "status",
        "created_at",
        "responded_at",
    )
    list_filter = ("status",)
    search_fields = ("applicant_club__name", "tournament__name")
    raw_id_fields = ("responded_by",)
    readonly_fields = ("created_at",)


@admin.register(ClubPlayerPlan)
class ClubPlayerPlanAdmin(admin.ModelAdmin):
    """Админка клубных тарифов игроков."""

    list_display = (
        "name",
        "club",
        "is_active",
        "monthly_fee",
        "duration_days",
        "registration_limit_period",
        "has_unlimited_registrations",
        "max_tournaments_per_month",
    )
    list_filter = ("is_active", "club", "registration_limit_period")
    search_fields = ("name", "club__name")
    ordering = ("club", "sort_order", "name")


@admin.register(ClubMemberPlan)
class ClubMemberPlanAdmin(admin.ModelAdmin):
    """Админка назначений тарифов участникам клуба."""

    list_display = (
        "user_email",
        "user_last_name",
        "user_first_name",
        "plan",
        "status",
        "started_at",
        "ended_at",
        "auto_renew",
    )
    list_filter = ("status", "auto_renew", "plan__club")
    search_fields = (
        "club_member__user__email",
        "club_member__user__last_name",
        "club_member__user__first_name",
        "club_member__user__phone",
        "plan__name",
        "plan__club__name",
    )
    raw_id_fields = ("club_member", "assigned_by")

    @admin.display(description="Email", ordering="club_member__user__email")
    def user_email(self, obj: ClubMemberPlan) -> str:
        """Вернуть email пользователя участника клуба.

        Args:
            obj: Запись `ClubMemberPlan`.

        Returns:
            Email пользователя.
        """

        return cast(str, obj.club_member.user.email)

    @admin.display(description="Фамилия", ordering="club_member__user__last_name")
    def user_last_name(self, obj: ClubMemberPlan) -> str:
        """Вернуть фамилию пользователя участника клуба.

        Args:
            obj: Запись `ClubMemberPlan`.

        Returns:
            Фамилия пользователя.
        """

        return cast(str, obj.club_member.user.last_name)

    @admin.display(description="Имя", ordering="club_member__user__first_name")
    def user_first_name(self, obj: ClubMemberPlan) -> str:
        """Вернуть имя пользователя участника клуба.

        Args:
            obj: Запись `ClubMemberPlan`.

        Returns:
            Имя пользователя.
        """

        return cast(str, obj.club_member.user.first_name)

    def get_search_results(
        self,
        request: HttpRequest,
        queryset: QuerySet[ClubMemberPlan],
        search_term: str,
    ) -> tuple[QuerySet[ClubMemberPlan], bool]:
        """
        Выполнить поиск по email/ФИО/телефону, названию тарифа и клуба.

        Args:
            request: Текущий HTTP-запрос админки.
            queryset: Базовый queryset для фильтрации.
            search_term: Строка поиска из формы админки.

        Returns:
            Кортеж (queryset, may_have_duplicates).
        """

        if not search_term:
            return queryset, False

        tokens = [t for t in search_term.split() if t]
        if not tokens:
            return queryset, False

        combined_q = Q()
        for token in tokens:
            email_q = Q(club_member__user__email__icontains=token)

            name_q = Q()
            plan_name_q = Q()
            club_name_q = Q()
            for variant in _case_variants_for_cyrillic(token):
                name_q |= Q(club_member__user__last_name__contains=variant)
                name_q |= Q(club_member__user__first_name__contains=variant)
                plan_name_q |= Q(plan__name__contains=variant)
                club_name_q |= Q(plan__club__name__contains=variant)

            token_q = email_q | name_q | plan_name_q | club_name_q

            phone_variants = _phone_variants_for_search(token)
            if phone_variants:
                phone_q = Q()
                for variant in phone_variants:
                    phone_q |= Q(club_member__user__phone__contains=variant)
                token_q |= phone_q

            combined_q &= token_q

        return queryset.filter(combined_q), False


@admin.register(ClubPlanTournamentAccess)
class ClubPlanTournamentAccessAdmin(admin.ModelAdmin):
    """Админка доступа тарифов к турнирам клуба."""

    list_display = ("plan", "tournament", "is_allowed", "updated_at")
    list_filter = ("is_allowed", "plan__club")
    search_fields = ("plan__name", "plan__club__name", "tournament__name")


@admin.register(ClubPlanSlotUsage)
class ClubPlanSlotUsageAdmin(admin.ModelAdmin):
    """Админка учёта лимитов тарифов."""

    list_display = (
        "club_member",
        "plan",
        "period_year",
        "period_month",
        "tournaments_used",
    )
    list_filter = ("period_year", "period_month", "plan__club")
    search_fields = ("club_member__user__email", "plan__name", "plan__club__name")
    raw_id_fields = ("club_member",)


# ---------------------------------------------------------------------------
# Аудит-лог и настройки платформы
# ---------------------------------------------------------------------------


@admin.register(PlatformPlan)
class PlatformPlanAdmin(admin.ModelAdmin):
    """Редактируемые тарифы платформы (Старт/Базовый/Про)."""

    list_display = (
        "name",
        "price_monthly",
        "price_yearly",
        "max_tournaments_per_month",
        "max_members",
        "trial_days",
        "is_public_page",
        "is_open_interclub",
        "is_active",
        "sort_order",
    )
    list_editable = (
        "price_monthly",
        "price_yearly",
        "max_tournaments_per_month",
        "max_members",
        "trial_days",
        "is_public_page",
        "is_open_interclub",
        "is_active",
        "sort_order",
    )
    search_fields = ("name",)
    ordering = ("sort_order", "slug")
    exclude = ("slug",)
    fieldsets = (
        (
            None,
            {
                "fields": ("name", "description", "is_active", "sort_order"),
            },
        ),
        (
            "Цены",
            {
                "fields": ("price_monthly", "price_yearly"),
            },
        ),
        (
            "Возможности",
            {
                "fields": (
                    "max_tournaments_per_month",
                    "max_members",
                    "trial_days",
                    "is_public_page",
                    "is_open_interclub",
                ),
            },
        ),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запрет добавления: тарифы создаются миграцией, slug менять нельзя."""
        return False


@admin.register(PlatformAuditLog)
class PlatformAuditLogAdmin(admin.ModelAdmin):
    """Аудит-лог действий platform_admin (только чтение)."""

    list_display = ("action", "actor", "club", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("club__name", "actor__email", "details")
    readonly_fields = ("action", "actor", "club", "details", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    """Глобальные настройки платформы (singleton)."""

    list_display = (
        "__str__",
        "trial_days",
        "suspended_data_retention_days",
        "auto_delete_suspended",
        "registration_open",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return not PlatformSettings.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


@admin.register(ClubNotificationSettings)
class ClubNotificationSettingsAdmin(admin.ModelAdmin):
    """Настройки уведомлений участника клуба."""

    list_display = ("user", "club", "is_enabled", "email_enabled", "telegram_enabled")
    list_filter = ("is_enabled", "email_enabled", "telegram_enabled")
    search_fields = ("user__email", "club__name")
    raw_id_fields = ("user",)


@admin.register(ClubNotificationConfig)
class ClubNotificationConfigAdmin(admin.ModelAdmin):
    """Настройки уведомлений клуба."""

    list_display = ("club", "notify_by_email", "notify_by_telegram")
    search_fields = ("club__name",)
