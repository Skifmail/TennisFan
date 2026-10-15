"""Просмотр и безопасный экспорт доказательств юридических волеизъявлений."""

from __future__ import annotations

import json
from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.utils.html import format_html

from .models import LegalAcceptanceLog


def _csv_safe(value: Any) -> str:
    text = str(value)
    return "'" + text if text.lstrip()[:1] in ("=", "+", "-", "@") else text


@admin.register(LegalAcceptanceLog)
class LegalAcceptanceLogAdmin(admin.ModelAdmin):
    """Закрытый журнал независимых акцептов, отзывов и действий оператора.

    Attributes:
        readonly_fields: Доказательства, недоступные для ручного редактирования.
    """

    list_display = (
        "user",
        "document_slug",
        "document_version",
        "source",
        "chat_context",
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
        "metadata__subject__email",
        "metadata__city_label",
        "metadata__acceptance_id",
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
        "chat_context",
        "document_snapshot",
    )
    actions = ("export_as_csv",)

    @admin.display(description="Город / чат")
    def chat_context(self, obj: LegalAcceptanceLog) -> str:
        """Показывает выбранный город и идентификатор комнаты.

        Args:
            obj: Запись журнала.

        Returns:
            Контекст чата либо тире для других согласий.
        """
        return (
            f"{obj.metadata.get('city_label', '')} / #{obj.metadata['room_id']}"
            if obj.metadata.get("room_id")
            else "—"
        )

    @admin.display(description="Сохранённый текст документа")
    def document_snapshot(self, obj: LegalAcceptanceLog) -> str:
        """Показывает зафиксированный текст, а не изменившуюся текущую редакцию.

        Args:
            obj: Запись журнала.

        Returns:
            Безопасно экранированный сохранённый текст.
        """
        return str(
            format_html(
                '<pre style="white-space:pre-wrap">{}</pre>',
                obj.metadata.get(
                    "document_text", "Для этой старой записи снимок не сохранялся."
                ),
            )
        )

    @admin.action(description="Выгрузить выбранные записи в CSV")
    def export_as_csv(
        self,
        request: HttpRequest,
        queryset: QuerySet[LegalAcceptanceLog],
    ) -> HttpResponse:
        """Выгружает доказательства с защитой от формул в табличных редакторах.

        Args:
            request: Запрос уполномоченного администратора.
            queryset: Выбранные записи журнала.

        Returns:
            CSV с реквизитами, браузером и полными сохранёнными метаданными.
        """
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
                "user_agent",
                "metadata",
            ]
        )
        for log in queryset.select_related("user"):
            snapshot = log.metadata.get("subject")
            subject = snapshot if isinstance(snapshot, dict) else {}
            writer.writerow(
                [
                    _csv_safe(value)
                    for value in [
                        (subject.get("email", log.user.email if log.user else "")),
                        (
                            subject.get(
                                "name", log.user.get_full_name() if log.user else ""
                            )
                        ),
                        log.get_document_slug_display(),
                        log.document_version,
                        log.source,
                        log.ip_address or "",
                        log.accepted_at.isoformat(),
                        log.user_agent,
                        json.dumps(log.metadata, ensure_ascii=False),
                    ]
                ]
            )
        return response

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запрещает создавать согласия за игрока.

        Args:
            request: Запрос администратора.

        Returns:
            Всегда False.
        """
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: LegalAcceptanceLog | None = None
    ) -> bool:
        """Запрещает редактировать ранее полученные доказательства.

        Args:
            request: Запрос администратора.
            obj: Запись журнала.

        Returns:
            Всегда False.
        """
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: LegalAcceptanceLog | None = None
    ) -> bool:
        """Оставляет удаление доказательств только суперпользователю.

        Args:
            request: Запрос администратора.
            obj: Запись журнала.

        Returns:
            Наличие полномочий суперпользователя.
        """
        return bool(request.user.is_superuser)
