"""
Core admin.
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import Feedback, FeedbackReply, SupportMessage


class FeedbackReplyInline(admin.TabularInline):
    model = FeedbackReply
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "subject_short", "created_at", "has_telegram_id")
    list_filter = ("created_at",)
    search_fields = ("user__email", "message", "subject")
    readonly_fields = ("created_at", "telegram_message_id")
    inlines = [FeedbackReplyInline]

    def subject_short(self, obj):
        return (obj.subject or obj.message[:50] or "—") + ("…" if obj.subject and len(obj.subject) > 50 else "")

    subject_short.short_description = "Тема / сообщение"

    def has_telegram_id(self, obj):
        return bool(obj.telegram_message_id)

    has_telegram_id.boolean = True
    has_telegram_id.short_description = "В Telegram"


@admin.register(FeedbackReply)
class FeedbackReplyAdmin(admin.ModelAdmin):
    list_display = ("id", "feedback", "text_short", "created_at")
    list_filter = ("created_at",)
    readonly_fields = ("created_at",)

    def text_short(self, obj):
        return (obj.text or "")[:60] + ("…" if len(obj.text or "") > 60 else "")


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "subject_short", "is_from_admin", "created_at", "has_admin_msg_id")
    list_filter = ("is_from_admin", "created_at")
    search_fields = ("user__email", "text", "subject")
    readonly_fields = ("created_at", "admin_telegram_message_id")

    def subject_short(self, obj):
        return (obj.subject or obj.text[:50] or "—") + ("…" if (obj.text or "") and len(obj.text or "") > 50 else "")
    subject_short.short_description = "Тема / сообщение"

    def has_admin_msg_id(self, obj):
        return bool(obj.admin_telegram_message_id)
    has_admin_msg_id.boolean = True
    has_admin_msg_id.short_description = "В Telegram"
