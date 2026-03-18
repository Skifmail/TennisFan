"""
Регистрация моделей клубного раздела в Django Admin.

Расширенная конфигурация для platform_admin: инлайны, действия (блокировка, смена тарифа),
финансовая сводка, аудит-лог, глобальные настройки платформы.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib import admin, messages
from django.db.models import Count, QuerySet, Sum
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone

from .models import (
    Club,
    ClubFeePayment,
    ClubInviteLink,
    ClubMember,
    ClubMemberPlan,
    ClubMembershipFee,
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
    ClubTournamentApplication,
    PlatformAuditLog,
    PlatformPlan,
    PlatformSettings,
)
from .services import log_platform_action

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
    fields = ("amount", "currency", "period", "is_active", "payment_provider")
    show_change_link = True


class ClubNotificationConfigInline(admin.StackedInline):
    """Настройки уведомлений клуба (inline)."""

    model = ClubNotificationConfig
    extra = 0
    can_delete = False


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


@admin.register(Club)
class ClubAdmin(admin.ModelAdmin):
    """Расширенная админка клуба для platform_admin."""

    change_list_template = "admin/clubs/club_changelist.html"
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
    )
    inlines = [
        ClubSubscriptionInline,
        ClubMemberInline,
        ClubMembershipFeeInline,
        ClubNotificationConfigInline,
    ]
    actions = ["block_clubs", "unblock_clubs", "reset_trial"]

    def get_urls(self):
        custom_urls = [
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

    list_display = ("user", "club", "role", "status", "joined_at", "created_at")
    list_filter = ("role", "status", "club")
    search_fields = ("user__email", "club__name")
    raw_id_fields = ("user", "invited_by")
    readonly_fields = ("created_at",)


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
        "max_tournaments_per_month",
    )
    list_filter = ("is_active", "club")
    search_fields = ("name", "club__name")
    ordering = ("club", "sort_order", "name")


@admin.register(ClubMemberPlan)
class ClubMemberPlanAdmin(admin.ModelAdmin):
    """Админка назначений тарифов участникам клуба."""

    list_display = (
        "club_member",
        "plan",
        "status",
        "started_at",
        "ended_at",
        "auto_renew",
    )
    list_filter = ("status", "auto_renew", "plan__club")
    search_fields = ("club_member__user__email", "plan__name", "plan__club__name")
    raw_id_fields = ("club_member", "assigned_by")


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
