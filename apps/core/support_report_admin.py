"""Рабочее место поддержки для сообщений об ошибках и ответов в общий диалог."""

from typing import Any, cast

from django import forms
from django.contrib import admin
from django.db import transaction
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from apps.users.models import User

from .models import BugReport
from .support_reports import reply_to_report


class BugReportForm(forms.ModelForm):
    """Добавляет отдельный ответ без изменения текста исходного обращения."""

    reply = forms.CharField(
        label="Ответ пользователю",
        required=False,
        max_length=6000,
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": "Уточните детали или сообщите, что исправлено. Ответ появится в диалоге поддержки.",
            }
        ),
    )

    class Meta:
        model = BugReport
        fields = ["status"]


@admin.register(BugReport)
class BugReportAdmin(admin.ModelAdmin):
    """Показывает автора, описание, изображения, статус и историю ответов."""

    form = BugReportForm
    list_display = (
        "id",
        "description_short",
        "author",
        "status",
        "created_at",
        "open_dialog",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "message__text",
        "message__user__email",
        "message__guest_name",
        "message__guest_contact",
        "page_url",
    )
    readonly_fields = (
        "author",
        "description",
        "screenshots",
        "page_link",
        "conversation",
        "created_at",
        "updated_at",
        "open_dialog",
    )
    fieldsets = (
        (
            "Обращение",
            {
                "fields": (
                    "author",
                    "created_at",
                    "description",
                    "page_link",
                    "screenshots",
                    "open_dialog",
                )
            },
        ),
        ("Работа с ошибкой", {"fields": ("status", "reply")}),
        ("Переписка", {"fields": ("conversation", "updated_at")}),
    )
    list_per_page = 30
    actions = None

    def get_queryset(self, request: HttpRequest):
        return (
            super()
            .get_queryset(request)
            .select_related("message__thread__user", "message__user")
            .prefetch_related("message__attachments")
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @admin.display(description="Описание")
    def description_short(self, obj: BugReport) -> str:
        return str(obj.message.text[:100])

    @admin.display(description="Автор")
    def author(self, obj: BugReport) -> str:
        return (
            str(obj.message.user)
            if obj.message.user_id
            else f"{obj.message.guest_name} · {obj.message.guest_contact}"
        )

    @admin.display(description="Что произошло")
    def description(self, obj: BugReport):
        return format_html(
            '<div style="white-space:pre-wrap;max-width:80ch">{}</div>',
            obj.message.text,
        )

    @admin.display(description="Скриншоты")
    def screenshots(self, obj: BugReport):
        return (
            format_html_join(
                "",
                '<a href="{}" target="_blank" rel="noopener"><img src="{}" alt="Скриншот ошибки" style="width:220px;max-height:180px;object-fit:contain;margin:6px;border:1px solid #777;border-radius:6px"></a>',
                (
                    (
                        reverse("support_attachment", args=[item.pk]),
                        reverse("support_attachment", args=[item.pk]),
                    )
                    for item in obj.message.attachments.all()
                ),
            )
            or "Без изображений"
        )

    @admin.display(description="Страница")
    def page_link(self, obj: BugReport):
        return (
            format_html(
                '<a href="{}" target="_blank" rel="noopener">{}</a>',
                obj.page_url,
                obj.page_url,
            )
            if obj.page_url
            else "Не указана"
        )

    @admin.display(description="Диалог поддержки")
    def open_dialog(self, obj: BugReport):
        return format_html(
            '<a href="{}#support-message-{}">Открыть диалог →</a>',
            reverse("support_admin_thread", args=[obj.message.thread_id]),
            obj.message_id,
        )

    @admin.display(description="История диалога")
    def conversation(self, obj: BugReport):
        return format_html_join(
            "",
            '<article style="padding:12px;border-bottom:1px solid #888"><strong>{} · {}</strong><div style="white-space:pre-wrap">{}</div></article>',
            (
                (
                    "Поддержка" if item.is_from_admin else "Пользователь",
                    item.created_at.strftime("%d.%m.%Y %H:%M"),
                    item.text,
                )
                for item in obj.message.thread.messages.order_by("created_at", "pk")
            ),
        )

    def save_model(
        self, request: HttpRequest, obj: BugReport, form: Any, change: bool
    ) -> None:
        super().save_model(request, obj, form, change)
        text = form.cleaned_data.get("reply", "").strip()
        if text:
            message = reply_to_report(
                actor=cast(User, request.user), report_id=obj.pk, text=text
            )
            from .support_notifications import send_user_support_reply_async

            transaction.on_commit(lambda: send_user_support_reply_async(message))
