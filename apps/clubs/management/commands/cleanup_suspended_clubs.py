"""
Management-команда для удаления suspended клубов с истёкшим retention-периодом.

Запускается ежедневно через CRON. Проверяет PlatformSettings.auto_delete_suspended
и удаляет клубы, находящиеся в статусе suspended дольше suspended_data_retention_days.
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max
from django.utils import timezone

from apps.clubs.models import (
    Club,
    ClubStatus,
    PlatformSettings,
)
from apps.clubs.services import log_platform_action

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Удаляет suspended клубы с истёкшим retention-периодом (если включено в PlatformSettings)"

    def handle(self, *args, **options):
        ps = PlatformSettings.load()

        if not ps.auto_delete_suspended:
            self.stdout.write("Автоудаление suspended клубов выключено. Пропускаю.")
            return

        retention_days = ps.suspended_data_retention_days
        cutoff = timezone.now() - timedelta(days=retention_days)

        suspended_clubs = Club.objects.filter(status=ClubStatus.SUSPENDED)

        deleted_count = 0
        for club in suspended_clubs:
            last_sub_end = club.subscriptions.aggregate(latest=Max("ends_at"))["latest"]

            if last_sub_end and last_sub_end < cutoff:
                club_name = club.name
                log_platform_action(
                    actor=None,
                    action="club_auto_deleted",
                    club=club,
                    details=f"Автоудаление: suspended дольше {retention_days} дней. "
                    f"Подписка истекла {last_sub_end:%d.%m.%Y}.",
                )
                club.delete()
                deleted_count += 1
                logger.info("Клуб '%s' удалён (auto_delete_suspended).", club_name)

        self.stdout.write(
            self.style.SUCCESS(f"Удалено suspended клубов: {deleted_count}")
        )
