"""
Core admin.
"""
from django.contrib import admin
from django.db.models import Count, Max, Q
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from .models import SupportConversation, SupportMessage, TelegramTransferConsentLog


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
        # Группируем сообщения по пользователям
        conversations = (
            SupportMessage.objects.values("user", "user__email", "user__first_name", "user__last_name")
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

        support_messages = SupportMessage.objects.filter(user=user).order_by("created_at")

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
    search_fields = ("user__email", "user__first_name", "user__last_name", "ip_address", "user_agent")
    readonly_fields = ("user", "consent_version", "ip_address", "user_agent", "consented_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
