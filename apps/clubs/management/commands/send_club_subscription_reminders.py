"""
Management-команда для отправки напоминаний о подписке клуба (за 7 и 3 дня до окончания).
"""

import logging

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import ClubPlan, ClubSubscription, ClubSubscriptionStatus
from apps.clubs.notifications import send_subscription_expiring_notifications

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Отправка напоминаний администраторам клубов о подписке."""

    help = "Отправляет напоминания администраторам клубов о подписке (за 7 и 3 дня до окончания)."

    def handle(self, *args, **options):
        """Основная логика команды."""
        today = timezone.now().date()
        sent = 0

        active_subs = ClubSubscription.objects.filter(
            status=ClubSubscriptionStatus.ACTIVE
        ).select_related("club")
        for sub in active_subs:
            ends_at = sub.ends_at.date() if sub.ends_at else None
            if not ends_at:
                continue

            days_left = (ends_at - today).days
            if days_left not in (7, 3):
                continue

            club = sub.club
            plan_name = dict(ClubPlan.choices).get(sub.plan, sub.plan)
            ends_at_str = ends_at.strftime("%d.%m.%Y")

            try:
                renew_url = reverse(
                    "clubs:subscription_pay", kwargs={"slug": club.slug}
                )
            except Exception:
                renew_url = ""

            send_subscription_expiring_notifications(
                club=club,
                days_left=days_left,
                plan_name=plan_name,
                ends_at=ends_at_str,
                renew_url=renew_url,
            )
            sent += 1

        self.stdout.write(
            self.style.SUCCESS(f"Напоминания о подписке отправлены: {sent}.")
        )
        logger.info("send_club_subscription_reminders: sent=%d", sent)
