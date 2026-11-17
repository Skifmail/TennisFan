"""Заполнить дедлайны подтверждения у уже существующих откликов и заявок."""

from __future__ import annotations

from datetime import timedelta

from django.db import migrations
from django.db.models import Min
from django.utils import timezone

CONFIRMATION_HOURS = 48


def backfill(apps, schema_editor) -> None:
    """Проставить срок ожидания старым откликам и дедлайн их заявкам."""
    SparringResponse = apps.get_model("sparring", "SparringResponse")
    SparringRequest = apps.get_model("sparring", "SparringRequest")

    pending = SparringResponse.objects.filter(
        status="pending", expires_at__isnull=True
    ).only("id", "created_at", "expires_at")
    for response in pending.iterator():
        response.expires_at = response.created_at + timedelta(hours=CONFIRMATION_HOURS)
        response.save(update_fields=["expires_at"])

    active_requests = SparringRequest.objects.filter(
        status="active", match__isnull=True
    ).only("id", "confirmation_deadline")
    for request in active_requests.iterator():
        earliest = SparringResponse.objects.filter(
            sparring_request_id=request.pk,
            status="pending",
            expires_at__isnull=False,
        ).aggregate(value=Min("expires_at"))["value"]
        if earliest is None:
            continue
        request.confirmation_deadline = max(earliest, timezone.now())
        request.save(update_fields=["confirmation_deadline"])


def noop(apps, schema_editor) -> None:
    """Откат не требуется: исходные значения не сохраняются."""


class Migration(migrations.Migration):
    """Миграция данных для авто-принятия старых откликов."""

    dependencies = [
        ("sparring", "0019_sparringmatchreminder_sparringrequest_auto_accept_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
