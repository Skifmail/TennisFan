"""
Core admin.
"""

from html import escape
from typing import ClassVar, cast

from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .models import (
    City,
    ClubsOutboundEmail,
    FooterSocialLink,
    GeoArea,
    LegalAcceptanceLog,
    NewTournamentOutboundEmail,
    OtherOutboundEmail,
    OutboundEmail,
    PlatformActivityEvent,
    RegistrationOutboundEmail,
    SecurityOutboundEmail,
    SubscriptionOutboundEmail,
    SupportMessage,
    SupportOutboundEmail,
    SupportThread,
    TelegramTransferConsentLog,
    TournamentOutboundEmail,
    UserConsent,
)


@admin.register(OutboundEmail)
class OutboundEmailAdmin(admin.ModelAdmin):
    """Журнал всех исходящих писем: просмотр HTML и текста."""

    list_display = (
        "sent_at",
        "to_email",
        "user_display",
        "subject_short",
        "category",
        "status",
    )
    list_select_related = ("user",)
    list_filter: ClassVar[tuple[str, ...]] = ("category", "status", "sent_at")
    category_filter: ClassVar[str | None] = None
    search_fields = (
        "to_email",
        "subject",
        "user__email",
        "user__first_name",
        "user__last_name",
        "body_text",
    )
    date_hierarchy = "sent_at"
    ordering = ("-sent_at", "-id")
    readonly_fields = (
        "user_display",
        "to_email",
        "from_email",
        "subject",
        "category",
        "status",
        "error_message",
        "sent_at",
        "links_for_check",
        "body_preview",
        "body_text",
        "body_html",
    )
    fields = (
        "sent_at",
        "status",
        "category",
        "user_display",
        "to_email",
        "from_email",
        "subject",
        "error_message",
        "links_for_check",
        "body_preview",
        "body_text",
        "body_html",
    )

    @admin.display(description="Пользователь", ordering="user__last_name")
    def user_display(self, obj: OutboundEmail) -> str:
        """Показать имя пользователя вместо email.

        Args:
            obj (OutboundEmail): Письмо.

        Returns:
            str: Имя и фамилия или email, если ФИО пустые; «—» без пользователя.
        """
        if not obj.user_id:
            return "—"
        return str(obj.user.get_display_name())

    def get_urls(self) -> list:
        """Добавить URL HTML-превью письма без двойного экранирования ссылок.

        Returns:
            list: URL-паттерны админки.
        """
        opts = self.model._meta
        custom = [
            path(
                "<path:object_id>/html-preview/",
                self.admin_site.admin_view(self.html_preview_view),
                name=f"{opts.app_label}_{opts.model_name}_html_preview",
            ),
        ]
        return cast(list, custom + super().get_urls())

    @xframe_options_sameorigin
    def html_preview_view(
        self,
        request: HttpRequest,
        object_id: str,
    ) -> HttpResponse:
        """Отдать HTML тела письма для iframe в карточке админки.

        Args:
            request (HttpRequest): Запрос администратора.
            object_id (str): ID записи ``OutboundEmail``.

        Returns:
            HttpResponse: HTML-тело письма.
        """
        obj = get_object_or_404(self.model, pk=object_id)
        if obj.body_html:
            response = HttpResponse(
                obj.body_html, content_type="text/html; charset=utf-8"
            )
        else:
            text = escape(obj.body_text or "")
            response = HttpResponse(
                f"<pre style='white-space:pre-wrap'>{text}</pre>",
                content_type="text/html; charset=utf-8",
            )
        response["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @admin.display(description="Ссылки для проверки")
    def links_for_check(self, obj: OutboundEmail) -> str:
        """Показать кликабельные URL из письма (уже без ``&amp;``).

        Args:
            obj (OutboundEmail): Письмо.

        Returns:
            str: HTML со списком ссылок или прочерк.
        """
        import html as html_lib
        import re
        from urllib.parse import unquote

        raw = obj.body_html or obj.body_text or ""
        if not raw:
            return "—"
        found: list[str] = []
        for match in re.finditer(
            r"""href\s*=\s*["']([^"']+)["']""",
            raw,
            flags=re.IGNORECASE,
        ):
            url = html_lib.unescape(unquote(match.group(1))).strip()
            if url and url not in found:
                found.append(url)
        if not found:
            for match in re.finditer(r"https?://[^\s<>\"']+", raw):
                url = html_lib.unescape(match.group(0).rstrip(").,;")).strip()
                if url and url not in found:
                    found.append(url)
        if not found:
            return "—"
        return cast(
            str,
            format_html_join(
                "",
                '<div style="margin:0 0 8px;word-break:break-all;">'
                '<a href="{0}" target="_blank" rel="noopener">{0}</a></div>',
                ((url,) for url in found),
            ),
        )

    @admin.display(description="Тема", ordering="subject")
    def subject_short(self, obj: OutboundEmail) -> str:
        """Краткая тема для списка.

        Args:
            obj (OutboundEmail): Письмо.

        Returns:
            str: Тема (обрезанная).
        """
        subject = obj.subject or "(без темы)"
        return subject if len(subject) <= 80 else f"{subject[:77]}..."

    @admin.display(description="Просмотр письма")
    def body_preview(self, obj: OutboundEmail) -> str:
        """HTML-превью письма в карточке админки.

        Args:
            obj (OutboundEmail): Письмо.

        Returns:
            str: Safe HTML с iframe или текстом.
        """
        if not obj.pk:
            return "—"
        if obj.body_html or obj.body_text:
            opts = self.model._meta
            preview_url = reverse(
                f"admin:{opts.app_label}_{opts.model_name}_html_preview",
                args=[obj.pk],
            )
            return cast(
                str,
                format_html(
                    '<iframe src="{}" '
                    'sandbox="allow-popups allow-popups-to-escape-sandbox '
                    'allow-top-navigation-by-user-activation" '
                    'style="width:100%;min-height:520px;border:1px solid #d0d7de;'
                    'border-radius:8px;background:#fff;"></iframe>',
                    preview_url,
                ),
            )
        return "—"

    def get_queryset(self, request: HttpRequest):
        """Ограничить queryset разделом для proxy-моделей.

        Args:
            request (HttpRequest): Запрос администратора.

        Returns:
            QuerySet: Письма выбранного раздела или все письма.
        """
        qs = super().get_queryset(request)
        if self.category_filter:
            return qs.filter(category=self.category_filter)
        return qs

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запретить ручное создание записей журнала."""
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        """Разрешить открытие карточки (поля только для чтения)."""
        return True

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        """Разрешить удаление старых писем суперпользователю."""
        return bool(request.user and request.user.is_superuser)


class _CategorizedOutboundEmailAdmin(OutboundEmailAdmin):
    """Базовый админ раздела писем (proxy-модель)."""

    list_filter: ClassVar[tuple[str, ...]] = ("status", "sent_at")


@admin.register(RegistrationOutboundEmail)
class RegistrationOutboundEmailAdmin(_CategorizedOutboundEmailAdmin):
    """Раздел: регистрация и подтверждение почты."""

    category_filter = "registration"


@admin.register(NewTournamentOutboundEmail)
class NewTournamentOutboundEmailAdmin(_CategorizedOutboundEmailAdmin):
    """Раздел: уведомления о новых турнирах."""

    category_filter = "new_tournament"


@admin.register(TournamentOutboundEmail)
class TournamentOutboundEmailAdmin(_CategorizedOutboundEmailAdmin):
    """Раздел: письма по турнирам."""

    category_filter = "tournament"


@admin.register(SubscriptionOutboundEmail)
class SubscriptionOutboundEmailAdmin(_CategorizedOutboundEmailAdmin):
    """Раздел: подписки и платежи."""

    category_filter = "subscription"


@admin.register(SecurityOutboundEmail)
class SecurityOutboundEmailAdmin(_CategorizedOutboundEmailAdmin):
    """Раздел: безопасность аккаунта."""

    category_filter = "security"


@admin.register(ClubsOutboundEmail)
class ClubsOutboundEmailAdmin(_CategorizedOutboundEmailAdmin):
    """Раздел: клубные письма."""

    category_filter = "clubs"


@admin.register(SupportOutboundEmail)
class SupportOutboundEmailAdmin(_CategorizedOutboundEmailAdmin):
    """Раздел: поддержка."""

    category_filter = "support"


@admin.register(OtherOutboundEmail)
class OtherOutboundEmailAdmin(_CategorizedOutboundEmailAdmin):
    """Раздел: прочие письма."""

    category_filter = "other"


@admin.register(PlatformActivityEvent)
class PlatformActivityEventAdmin(admin.ModelAdmin):
    """Админка ленты активности платформы (только просмотр).

    События формируются автоматически и не редактируются вручную, поэтому
    добавление и изменение записей через админку запрещены.
    """

    list_display = (
        "created_at",
        "actor_name",
        "actor_role",
        "event_type",
        "amount",
        "currency",
        "description",
    )
    list_filter = ("event_type", "actor_role", "currency", "created_at")
    search_fields = (
        "actor_name",
        "description",
        "actor__first_name",
        "actor__last_name",
        "actor__email",
    )
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = (
        "event_type",
        "actor",
        "actor_name",
        "actor_role",
        "description",
        "amount",
        "currency",
        "target_url",
        "metadata",
        "dedupe_key",
        "created_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запретить ручное добавление событий в журнал."""
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        """Запретить ручное изменение событий в журнале."""
        return False


class SupportMessageInline(admin.TabularInline):
    """Inline для сообщений в карточке диалога."""

    model = SupportMessage
    extra = 0
    readonly_fields = ("created_at",)
    fields = ("is_from_admin", "subject", "text", "created_at")


@admin.register(SupportThread)
class SupportThreadAdmin(admin.ModelAdmin):
    """Админка диалогов поддержки."""

    list_display = (
        "id",
        "user",
        "guest_email",
        "admin_unread_count",
        "user_unread_count",
        "last_message_at",
    )
    list_filter = ("is_closed", "last_message_at")
    search_fields = ("user__email", "guest_email", "guest_name")
    inlines = [SupportMessageInline]


@admin.register(TelegramTransferConsentLog)
class TelegramTransferConsentLogAdmin(admin.ModelAdmin):
    """Журнал согласий на передачу данных в Telegram."""

    list_display = ("user", "consent_version", "ip_address", "consented_at")
    list_filter = ("consent_version", "consented_at")
    search_fields = (
        "user__email",
        "user__first_name",
        "user__last_name",
        "ip_address",
        "user_agent",
    )
    readonly_fields = (
        "user",
        "consent_version",
        "ip_address",
        "user_agent",
        "consented_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return bool(request.user.is_superuser)


@admin.register(UserConsent)
class UserConsentAdmin(admin.ModelAdmin):
    """Журнал согласий пользователей (только просмотр)."""

    list_display = (
        "user_display",
        "consent_type",
        "club",
        "document_version",
        "accepted_at",
        "ip_address",
    )
    list_display_links = ("user_display",)
    list_select_related = ("user",)
    list_filter = ("consent_type", "accepted_at")
    search_fields = (
        "user__email",
        "user__first_name",
        "user__last_name",
        "ip_address",
        "document_version",
    )
    readonly_fields = (
        "user_display",
        "consent_type",
        "club",
        "document_version",
        "accepted_at",
        "ip_address",
        "user_agent",
    )
    fields = (
        "user_display",
        "consent_type",
        "club",
        "document_version",
        "accepted_at",
        "ip_address",
        "user_agent",
    )

    @admin.display(description="Пользователь", ordering="user__last_name")
    def user_display(self, obj: UserConsent) -> str:
        """Показать имя пользователя вместо email.

        Args:
            obj (UserConsent): Запись согласия.

        Returns:
            str: Имя и фамилия или email, если ФИО пустые.
        """
        if not obj.user_id:
            return "—"
        return str(obj.user.get_display_name())

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return True

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return bool(request.user.is_superuser)


@admin.register(LegalAcceptanceLog)
class LegalAcceptanceLogAdmin(admin.ModelAdmin):
    """Журнал акцептов юридических документов."""

    list_display = (
        "user",
        "document_slug",
        "document_version",
        "source",
        "ip_address",
        "accepted_at",
    )
    list_filter = ("document_slug", "source", "accepted_at")
    search_fields = (
        "user__email",
        "user__first_name",
        "user__last_name",
        "ip_address",
        "user_agent",
    )
    readonly_fields = (
        "user",
        "document_slug",
        "document_version",
        "source",
        "ip_address",
        "user_agent",
        "metadata",
        "accepted_at",
    )
    actions = ("export_as_csv",)

    @admin.action(description="Выгрузить выбранные записи в CSV")
    def export_as_csv(
        self,
        request: HttpRequest,
        queryset,
    ) -> HttpResponse:
        """Экспорт выбранных фиксаций согласий в CSV для отчётов."""
        import csv

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            'attachment; filename="legal_acceptances_export.csv"'
        )

        writer = csv.writer(response)
        writer.writerow(
            [
                "user_email",
                "user_name",
                "document",
                "version",
                "source",
                "ip_address",
                "accepted_at",
            ]
        )
        for log in queryset.select_related("user"):
            writer.writerow(
                [
                    log.user.email,
                    log.user.get_full_name() or "",
                    log.get_document_slug_display(),
                    log.document_version,
                    log.source,
                    log.ip_address or "",
                    log.accepted_at.isoformat(),
                ]
            )
        return response

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return bool(request.user.is_superuser)


@admin.register(FooterSocialLink)
class FooterSocialLinkAdmin(admin.ModelAdmin):
    """Редактирование ссылок на соцсети в футере: URL и иконка (SVG)."""

    list_display = ("name", "url", "order", "icon", "icon_path")
    list_editable = ("order",)
    list_display_links = ("name", "url")
    search_fields = ("name", "url")


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    """Справочник населённых пунктов для автодополнения на сайте."""

    list_display = ("name", "settlement_type", "region")
    list_filter = ("settlement_type",)
    search_fields = ("name", "region")
    ordering = ("name",)


@admin.register(GeoArea)
class GeoAreaAdmin(admin.ModelAdmin):
    """Зоны Москвы и города области: фильтры, лендинги и рекламные направления."""

    list_display = (
        "name",
        "region",
        "slug",
        "sort_order",
        "is_active",
        "is_advertised",
    )
    list_editable = ("sort_order", "is_active", "is_advertised")
    list_filter = ("region", "is_active", "is_advertised")
    search_fields = ("name", "slug", "aliases")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    ordering = ("region", "sort_order", "name")
