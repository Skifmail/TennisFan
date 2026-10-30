"""Сверка оплат, освобождение резервов и напоминания детской лиги."""

from datetime import timedelta

import requests
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from loguru import logger

from apps.juniors.billing import execute_refund, issue_payment, reconcile_payment
from apps.juniors.competition import expire_reservations
from apps.juniors.models import (
    JuniorEntry,
    JuniorPayment,
    JuniorRefund,
    JuniorTournament,
)
from apps.juniors.notifications import notify_family


class Command(BaseCommand):
    help = "Сверяет оплаты детской лиги, освобождает резервы и напоминает о турнирах"

    def handle(self, *args, **options):
        for payment in JuniorPayment.objects.filter(
            status__in=["new", "pending"]
        ).order_by("created_at")[:100]:
            try:
                if not payment.provider_id:
                    payment = issue_payment(payment)
                reconcile_payment(payment.pk)
            except (
                ValidationError,
                requests.RequestException,
                ValueError,
                RuntimeError,
            ) as exc:
                logger.warning(
                    "Детская оплата {} требует сверки ({})",
                    payment.pk,
                    type(exc).__name__,
                )
        for refund in JuniorRefund.objects.filter(
            status="processing", approved_by__isnull=False
        ).select_related("approved_by")[:100]:
            try:
                execute_refund(actor=refund.approved_by, refund_id=refund.pk)
            except (ValidationError, requests.RequestException, ValueError) as exc:
                logger.warning(
                    "Детский возврат {} требует сверки ({})",
                    refund.pk,
                    type(exc).__name__,
                )
        for tournament_id in JuniorTournament.objects.filter(
            status__in=["open", "closed"]
        ).values_list("pk", flat=True):
            with transaction.atomic():
                tournament = JuniorTournament.objects.select_for_update(
                    of=("self",)
                ).get(pk=tournament_id)
                expire_reservations(tournament)
        now = timezone.now()
        for entry_id in JuniorEntry.objects.filter(
            status="confirmed",
            reminder_sent_at__isnull=True,
            tournament__status__in=["open", "closed", "active"],
            tournament__starts_at__gt=now,
            tournament__starts_at__lte=now + timedelta(days=1),
        ).values_list("pk", flat=True):
            with transaction.atomic():
                entry = (
                    JuniorEntry.objects.select_for_update(of=("self",))
                    .select_related("child", "tournament")
                    .get(pk=entry_id)
                )
                if entry.reminder_sent_at is None and entry.status == "confirmed":
                    notify_family(
                        entry.child,
                        f"Скоро турнир «{entry.tournament.name}». Проверьте время и место в кабинете.",
                    )
                    entry.reminder_sent_at = now
                    entry.save(update_fields=["reminder_sent_at"])
