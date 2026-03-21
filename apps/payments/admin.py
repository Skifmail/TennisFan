"""Админка для платежей."""

from __future__ import annotations

import csv
from typing import Iterable

from django.contrib import admin
from django.http import HttpRequest, HttpResponse

from .models import PaymentRecord, SavedPaymentMethod


@admin.register(PaymentRecord)
class PaymentRecordAdmin(admin.ModelAdmin):
    """Журнал оплат: компактный список + экспорт в CSV."""

    list_display = (
        "user",
        "payment_type",
        "item_label",
        "amount",
        "currency",
        "status",
        "is_recurring",
        "autopay_enabled",
        "paid_at",
    )
    list_filter = (
        "payment_type",
        "status",
        "currency",
        "is_recurring",
        "autopay_enabled",
        "paid_at",
    )
    search_fields = (
        "user__email",
        "user__first_name",
        "user__last_name",
        "item_label",
        "yookassa_payment_id",
        "item_id",
    )
    date_hierarchy = "paid_at"
    readonly_fields = (
        "user",
        "payment_type",
        "item_id",
        "item_label",
        "amount",
        "currency",
        "status",
        "yookassa_payment_id",
        "is_recurring",
        "autopay_enabled",
        "metadata",
        "paid_at",
        "created_at",
    )
    actions = ("export_as_csv",)

    @admin.action(description="Выгрузить выбранные оплаты в CSV")
    def export_as_csv(
        self,
        request: HttpRequest,
        queryset: Iterable[PaymentRecord],
    ) -> HttpResponse:
        """Экспорт выбранных записей в CSV для отчётов по пользователю и периоду."""
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="payments_export.csv"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "user_email",
                "user_name",
                "payment_type",
                "item_id",
                "item_label",
                "amount",
                "currency",
                "status",
                "is_recurring",
                "autopay_enabled",
                "paid_at",
                "yookassa_payment_id",
            ]
        )
        for rec in queryset.select_related("user"):
            writer.writerow(
                [
                    rec.user.email,
                    rec.user.get_full_name() or "",
                    rec.get_payment_type_display(),
                    rec.item_id,
                    rec.item_label,
                    rec.amount,
                    rec.currency,
                    rec.status,
                    "yes" if rec.is_recurring else "no",
                    "yes" if rec.autopay_enabled else "no",
                    rec.paid_at.isoformat(),
                    rec.yookassa_payment_id,
                ]
            )
        return response


@admin.register(SavedPaymentMethod)
class SavedPaymentMethodAdmin(admin.ModelAdmin):
    """Просмотр сохранённых платёжных методов (карты ЮKassa)."""

    list_display = (
        "user",
        "payment_method_id",
        "card_network",
        "card_last4",
        "card_exp_month",
        "card_exp_year",
        "is_active",
        "is_default_for_subscriptions",
        "is_default_for_club_plans",
        "is_default_for_club_fees",
        "created_at",
    )
    list_filter = (
        "is_active",
        "is_default_for_subscriptions",
        "is_default_for_club_plans",
        "is_default_for_club_fees",
        "card_network",
    )
    search_fields = ("user__email", "payment_method_id", "card_last4")
    readonly_fields = (
        "user",
        "payment_method_id",
        "card_last4",
        "card_exp_month",
        "card_exp_year",
        "card_network",
        "is_active",
        "is_default_for_subscriptions",
        "is_default_for_club_plans",
        "is_default_for_club_fees",
        "created_at",
        "updated_at",
    )
