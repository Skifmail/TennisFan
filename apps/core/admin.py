"""
Core admin.
"""

from django.contrib import admin
from django.db.models import Count, Max, Q
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import path

from .models import (
    City,
    FooterSocialLink,
    LegalAcceptanceLog,
    SupportConversation,
    SupportMessage,
    TelegramTransferConsentLog,
)


@admin.register(SupportConversation)
class SupportConversationAdmin(admin.ModelAdmin):
    """
    Админка для диалогов поддержки.
    Показывает список пользователей с количеством сообщений.
    При клике открывается переписка с пользователем.
    """

    change_list_template = "admin/core/supportconversation/change_list.html"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        """Показываем список пользователей с диалогами."""
        # Группируем сообщения по пользователям (только зарегистрированные; гости — user=None)
        conversations = (
            SupportMessage.objects.filter(user__isnull=False)
            .values("user", "user__email", "user__first_name", "user__last_name")
            .annotate(
                message_count=Count("id"),
                last_message_at=Max("created_at"),
                unanswered_count=Count("id", filter=Q(is_from_admin=False)),
            )
            .order_by("-last_message_at")
        )

        context = {
            **self.admin_site.each_context(request),
            "title": "Диалоги поддержки",
            "conversations": conversations,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, self.change_list_template, context)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "conversation/<int:user_id>/",
                self.admin_site.admin_view(self.conversation_view),
                name="core_supportconversation_detail",
            ),
        ]
        return custom_urls + urls

    def conversation_view(self, request, user_id):
        """Показываем переписку с конкретным пользователем."""
        from apps.users.models import User

        user = User.objects.filter(pk=user_id).first()
        if not user:
            from django.http import Http404

            raise Http404("Пользователь не найден")

        support_messages = SupportMessage.objects.filter(user=user).order_by(
            "created_at"
        )

        context = {
            **self.admin_site.each_context(request),
            "title": f"Переписка с {user.get_full_name() or user.email}",
            "conversation_user": user,
            "support_messages": support_messages,
            "opts": self.model._meta,
        }
        return TemplateResponse(
            request, "admin/core/supportconversation/conversation_detail.html", context
        )


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

    def has_delete_permission(self, request, obj=None):
        return False


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

    def has_delete_permission(self, request, obj=None):
        return False


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
