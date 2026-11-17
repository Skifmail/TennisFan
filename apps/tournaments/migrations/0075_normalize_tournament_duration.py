"""Согласовать старые настройки без изменения явных дат и финансовой истории."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from django.apps.registry import Apps
from django.db import migrations, transaction
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.models import Model

BATCH_SIZE: Final = 1000
UPDATED_FIELDS: Final = ("duration", "is_one_day", "end_date", "allow_postpayment")


def normalize_tournament_duration(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    """Пакетно согласовать продолжительность, сохранив календарь и условия оплаты.

    Старый флаг уточняет только продолжительность по умолчанию. Явный календарь
    может расширить короткий формат, но не сокращается. Постоплата, открытое
    окно и незакрытые инвойсы не превращаются в однодневный режим оплаты.
    Для коротких событий заполняется лишь отсутствующая дата окончания;
    фактический старт после набора не назначается. Откат кода не возвращает
    противоречия, поскольку исходные значения однозначно не восстанавливаются.
    """
    tournaments = apps.get_model("tournaments", "Tournament")
    invoices = apps.get_model("tournaments", "TournamentPostpaymentInvoice")
    alias = schema_editor.connection.alias
    last_pk = 0

    while True:
        with transaction.atomic(using=alias):
            batch = list(
                tournaments.objects.using(alias)
                .select_for_update()
                .filter(pk__gt=last_pk)
                .order_by("pk")
                .only(
                    *UPDATED_FIELDS,
                    "start_date",
                    "entry_fee",
                    "postpayment_window_started_at",
                )[:BATCH_SIZE]
            )
            if not batch:
                break
            last_pk = batch[-1].pk
            pending_tournament_ids = set(
                invoices.objects.using(alias)
                .filter(tournament_id__in=[row.pk for row in batch], status="pending")
                .values_list("tournament_id", flat=True)
            )
            changed: list[Model] = []
            for row in batch:
                original = tuple(getattr(row, field) for field in UPDATED_FIELDS)
                duration = row.duration
                if duration == "multi" and row.is_one_day:
                    duration = "single"

                if row.start_date is not None and row.end_date is not None:
                    calendar_days = (row.end_date - row.start_date).days
                    if duration == "single" and calendar_days > 0:
                        duration = "weekend" if calendar_days == 1 else "multi"
                    elif duration == "weekend" and calendar_days > 1:
                        duration = "multi"

                has_payment_window = row.postpayment_window_started_at is not None
                has_pending_invoice = row.pk in pending_tournament_ids
                if (
                    row.allow_postpayment
                    and row.entry_fee <= 0
                    and not has_payment_window
                    and not has_pending_invoice
                ):
                    row.allow_postpayment = False
                if duration == "single" and (
                    row.allow_postpayment or has_payment_window or has_pending_invoice
                ):
                    duration = "multi"

                row.duration = duration
                row.is_one_day = duration == "single"
                if row.end_date is None and row.start_date is not None:
                    if duration == "single":
                        row.end_date = row.start_date
                    elif duration == "weekend":
                        row.end_date = row.start_date + timedelta(days=1)
                if tuple(getattr(row, field) for field in UPDATED_FIELDS) != original:
                    changed.append(row)
            if changed:
                tournaments.objects.using(alias).bulk_update(
                    changed, UPDATED_FIELDS, batch_size=BATCH_SIZE
                )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("tournaments", "0074_tournament_duration_labels")]
    operations = [
        migrations.RunPython(normalize_tournament_duration, migrations.RunPython.noop),
    ]
