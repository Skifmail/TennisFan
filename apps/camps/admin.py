"""Администрирование собственных программ и безопасные служебные ссылки."""

from __future__ import annotations

from typing import Any, cast

from django.contrib import admin
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    Camp,
    CampApplication,
    CampCancellation,
    CampExpense,
    CampGame,
    CampPackage,
    CampParticipant,
    CampPayment,
    CampReminder,
    CampSession,
    CampTournament,
)


@admin.register(Camp)
class CampAdmin(admin.ModelAdmin):
    """Редактор публичной программы."""

    list_display = ("title", "audience", "is_published", "updated_at")
    list_filter = ("is_published", "audience")
    search_fields = ("title",)
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ("coaches",)
    # Полный заезд создаётся отдельной формой: даты, вместимость и условия обязательны.
    readonly_fields = ("session_list",)

    @admin.display(description="Заезды")
    def session_list(self, obj: Camp) -> str:
        """Открывает создание заезда с выбранной программой."""
        if not obj.pk:
            return "Сохраните программу, затем добавьте заезды."
        return cast(
            str,
            format_html(
                '<a href="{}?camp={}">Добавить заезд →</a>',
                reverse("admin:camps_campsession_add"),
                obj.pk,
            ),
        )


class PackageInline(admin.TabularInline):
    """Пакеты, доступные для выбранного заезда."""

    model = CampPackage
    extra = 0


class ExpenseInline(admin.TabularInline):
    """План расходов по валютам."""

    model = CampExpense
    extra = 0


class TournamentInline(admin.TabularInline):
    """Связи с существующими турнирами платформы."""

    model = CampTournament
    extra = 0
    raw_id_fields = ("tournament",)


@admin.register(CampSession)
class CampSessionAdmin(admin.ModelAdmin):
    """Редактор заезда со ссылкой на рабочий экран команды."""

    list_display = ("camp", "city", "start_date", "status", "capacity", "team_link")
    list_filter = ("status", "country")
    list_select_related = ("camp",)
    inlines = (PackageInline, ExpenseInline, TournamentInline)
    readonly_fields = ("team_link",)
    raw_id_fields = ("court", "coordinator")

    @admin.display(description="Участники и заявки")
    def team_link(self, obj: CampSession) -> str:
        """Возвращает ссылку на управление заездом."""
        if not obj.pk:
            return "Сначала сохраните заезд."
        return cast(
            str,
            format_html(
                '<a href="{}">Открыть заезд →</a>',
                reverse("camps:manage_session", args=[obj.pk]),
            ),
        )


@admin.register(CampApplication)
class CampApplicationAdmin(admin.ModelAdmin):
    """Не позволяет обходить проверку мест обычным сохранением модели."""

    list_display = ("contact_name", "session", "status", "created_at", "manage_link")
    list_filter = ("status", "session")
    search_fields = ("contact_name", "email", "phone")
    list_select_related = ("session__camp",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Заявки создаются формой с полным составом поездки."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Сохраняет историю участия и финансов."""
        return False

    def get_readonly_fields(
        self, request: HttpRequest, obj: Any = None
    ) -> tuple[str, ...]:
        """Все изменения проходят через рабочий экран и бизнес-операции."""
        return tuple(field.name for field in self.model._meta.fields) + ("manage_link",)

    @admin.display(description="Работа с заявкой")
    def manage_link(self, obj: CampApplication) -> str:
        """Открывает подтверждение участия и журнал расчётов."""
        return cast(
            str,
            format_html(
                '<a href="{}">Открыть заявку →</a>',
                reverse("camps:manage_application", args=[obj.pk]),
            ),
        )


@admin.register(CampParticipant)
class CampParticipantAdmin(admin.ModelAdmin):
    """Позволяет назначать группы и профили без изменения подтверждённого состава."""

    list_display = ("name", "role", "application", "training_group")
    list_filter = ("application__session", "role", "training_group")
    search_fields = ("name",)
    raw_id_fields = ("player",)
    readonly_fields = ("application", "role")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Состав фиксируется при отправке заявки."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Не позволяет освободить место удалением участника."""
        return False


@admin.register(CampPayment)
class CampPaymentAdmin(admin.ModelAdmin):
    """Журнал только для чтения; исправления вносятся возвратной записью."""

    list_display = ("application", "amount", "reference", "recorded_by", "created_at")
    readonly_fields = (
        "application",
        "amount",
        "reference",
        "recorded_by",
        "created_at",
        "operation_key",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Проводки создаются через сервис с проверкой остатка."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Сохраняет неизменяемую историю ручного учёта."""
        return False


@admin.register(CampGame)
class CampGameAdmin(admin.ModelAdmin):
    """Журнал товарищеских игр, изолированный от рейтингового движка."""

    list_display = ("session", "player1", "player2", "score", "played_at")
    list_filter = ("session",)
    raw_id_fields = ("player1", "player2")


@admin.register(CampCancellation)
class CampCancellationAdmin(admin.ModelAdmin):
    """История запросов; решения принимаются только через сервис координатора."""

    list_display = ("application", "status", "created_at", "resolved_at", "manage_link")
    list_filter = ("status", "application__session")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запрос подаёт сам участник."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Сохраняет историю обращений."""
        return False

    def get_readonly_fields(
        self, request: HttpRequest, obj: Any = None
    ) -> tuple[str, ...]:
        """Защищает статус от изменения в обход проверки мест и уведомлений."""
        return tuple(field.name for field in self.model._meta.fields) + ("manage_link",)

    @admin.display(description="Рассмотреть")
    def manage_link(self, obj: CampCancellation) -> str:
        """Открывает единый рабочий экран заявки."""
        return cast(
            str,
            format_html(
                '<a href="{}">Открыть заявку →</a>',
                reverse("camps:manage_application", args=[obj.application_id]),
            ),
        )


@admin.register(CampReminder)
class CampReminderAdmin(admin.ModelAdmin):
    """Диагностика плановых событий, включая неопределённую SMTP-доставку."""

    list_display = ("application", "kind", "basis", "status", "attempts", "updated_at")
    list_filter = ("status", "kind")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Поводы создаёт планировщик."""
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """Не позволяет повторить доставку удалением отметки."""
        return False

    def get_readonly_fields(
        self, request: HttpRequest, obj: Any = None
    ) -> tuple[str, ...]:
        """Журнал доступен только для чтения."""
        return tuple(field.name for field in self.model._meta.fields)
