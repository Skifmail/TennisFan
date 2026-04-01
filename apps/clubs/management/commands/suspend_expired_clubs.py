"""
Management-команда для перевода клубов с истёкшей подпиской в статус suspended.

Запускается ежедневно через CRON. Обрабатывает:
- Клубы со статусом active, у которых нет активной подписки (все ended).
- Клубы со статусом trial, у которых trial_ends_at < now.
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.clubs.models import (
    Club,
    ClubStatus,
    ClubSubscriptionStatus,
)
from apps.clubs.services import log_platform_action

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Переводит клубы с истёкшей подпиской/trial в suspended"

    def handle(self, *args, **options):
        now = timezone.now()
        suspended_count = 0

        active_clubs = Club.objects.filter(status=ClubStatus.ACTIVE)
        for club in active_clubs:
            has_active_sub = club.subscriptions.filter(
                status=ClubSubscriptionStatus.ACTIVE,
                ends_at__gt=now,
            ).exists()

            if not has_active_sub:
                club.status = ClubStatus.SUSPENDED
                club.save(update_fields=["status"])
                log_platform_action(
                    actor=None,
                    action="club_auto_suspended",
                    club=club,
                    details="Автоматическая приостановка: нет активной подписки.",
                )
                logger.info(
                    "Клуб '%s' переведён в suspended (нет активной подписки).",
                    club.name,
                )
                suspended_count += 1

        trial_clubs = Club.objects.filter(
            status=ClubStatus.TRIAL,
            trial_ends_at__lt=now,
        )
        for club in trial_clubs:
            has_active_sub = club.subscriptions.filter(
                status=ClubSubscriptionStatus.ACTIVE,
                ends_at__gt=now,
            ).exists()
            if has_active_sub:
                club.status = ClubStatus.ACTIVE
                club.save(update_fields=["status"])
                logger.info(
                    "Клуб '%s' переведён в active (trial истёк, но активная подписка действует).",
                    club.name,
                )
                continue
            club.status = ClubStatus.SUSPENDED
            club.save(update_fields=["status"])
            log_platform_action(
                actor=None,
                action="club_auto_suspended",
                club=club,
                details=f"Автоматическая приостановка: trial истёк {club.trial_ends_at:%d.%m.%Y}.",
            )
            logger.info("Клуб '%s' переведён в suspended (trial истёк).", club.name)
            suspended_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Переведено в suspended: {suspended_count}")
        )
