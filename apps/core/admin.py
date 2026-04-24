"""
Core admin.
"""

from django.contrib import admin
from django.http import HttpRequest, HttpResponse

from .models import (
    City,
    FooterSocialLink,
    LegalAcceptanceLog,
    SupportMessage,
    SupportThread,
    TelegramTransferConsentLog,
    UserConsent,
)


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
        "user",
        "consent_type",
        "club",
        "document_version",
        "accepted_at",
        "ip_address",
    )
    list_filter = ("consent_type", "accepted_at")
    search_fields = (
        "user__email",
        "user__first_name",
        "user__last_name",
        "ip_address",
        "document_version",
    )
    readonly_fields = (
        "user",
        "consent_type",
        "club",
        "document_version",
        "accepted_at",
        "ip_address",
        "user_agent",
    )

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
    """Справочник городов для автодополнения на сайте."""

    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)
