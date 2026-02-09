"""
Уведомления участникам о начале турнира (в день start_date).

Находит турниры, у которых start_date = сегодня, и отправляет участникам
сообщение в Telegram (пользовательский бот) и создаёт уведомление в ЛК.

Запуск: python manage.py send_tournament_start_reminders

Рекомендуется добавить в cron раз в день утром (например в 08:00):
  0 8 * * * cd /path && python manage.py send_tournament_start_reminders
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.telegram_bot import notifications as tg
from apps.tournaments.models import Tournament

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Отправить уведомления участникам турниров, которые начинаются сегодня (бот + ЛК)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Не отправлять сообщения, только вывести турниры.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        today = timezone.now().date()

        tournaments = list(
            Tournament.objects.filter(
                start_date=today,
                bracket_generated=True,
            ).exclude(status="cancelled")
        )

        if not tournaments:
            self.stdout.write("Нет турниров с датой начала сегодня.")
            return

        sent = 0
        for tournament in tournaments:
            if dry_run:
                self.stdout.write(f"  Турнир: {tournament.name} (slug={tournament.slug})")
                continue
            try:
                tg.notify_tournament_start(tournament)
                sent += 1
            except Exception as e:
                logger.exception("send_tournament_start_reminders %s: %s", tournament.slug, e)

        if dry_run:
            self.stdout.write(f"Dry-run: турниров «начало сегодня»: {len(tournaments)}")
        else:
            self.stdout.write(f"Уведомлений о начале турнира отправлено: {sent}.")
