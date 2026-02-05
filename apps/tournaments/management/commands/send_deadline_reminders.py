"""
Напоминания о дедлайне матча за 2 и 1 день.

Выбирает матчи, у которых deadline попадает в окно «через 2 дня» (47–49 ч)
и «через 1 день» (23–25 ч), и отправляет участникам сообщение в Telegram
(пользовательский бот) с кнопками «Внести результат», «Мои матчи», «Запросить продление».

Запуск: python manage.py send_deadline_reminders

Рекомендуется добавить в cron раз в день (например в 09:00):
  0 9 * * * cd /path && python manage.py send_deadline_reminders
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.telegram_bot import notifications as tg
from apps.telegram_bot import services as bot_services
from apps.tournaments.models import Match

logger = logging.getLogger(__name__)

# Окна: напоминание «за 2 дня» — deadline через 47–49 ч, «за 1 день» — через 23–25 ч
HOURS_LOW_2 = 47
HOURS_HIGH_2 = 49
HOURS_LOW_1 = 23
HOURS_HIGH_1 = 25


class Command(BaseCommand):
    help = "Отправить напоминания о дедлайне матча за 2 и 1 день в Telegram участникам."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Не отправлять сообщения, только вывести матчи.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        if not dry_run and not bot_services.is_configured():
            self.stdout.write("Telegram user bot не настроен (TELEGRAM_USER_BOT_TOKEN).")
            return

        now = timezone.now()

        # Окно «через 2 дня»: deadline в [now+47h, now+49h]
        low_2 = now + timedelta(hours=HOURS_LOW_2)
        high_2 = now + timedelta(hours=HOURS_HIGH_2)
        matches_2d = list(
            Match.objects.filter(
                deadline__isnull=False,
                deadline__gte=low_2,
                deadline__lte=high_2,
                status=Match.MatchStatus.SCHEDULED,
            ).select_related("tournament", "player1", "player2", "team1", "team2")
        )

        # Окно «через 1 день»: deadline в [now+23h, now+25h]
        low_1 = now + timedelta(hours=HOURS_LOW_1)
        high_1 = now + timedelta(hours=HOURS_HIGH_1)
        matches_1d = list(
            Match.objects.filter(
                deadline__isnull=False,
                deadline__gte=low_1,
                deadline__lte=high_1,
                status=Match.MatchStatus.SCHEDULED,
            ).select_related("tournament", "player1", "player2", "team1", "team2")
        )

        sent_2 = 0
        sent_1 = 0

        for match in matches_2d:
            if dry_run:
                self.stdout.write(f"  [2d] Матч #{match.pk} {match} дедлайн {match.deadline}")
            else:
                try:
                    tg.notify_match_deadline_reminder(match, days_left=2)
                    sent_2 += 1
                except Exception as e:
                    logger.exception("send_deadline_reminder 2d match %s: %s", match.pk, e)

        for match in matches_1d:
            if dry_run:
                self.stdout.write(f"  [1d] Матч #{match.pk} {match} дедлайн {match.deadline}")
            else:
                try:
                    tg.notify_match_deadline_reminder(match, days_left=1)
                    sent_1 += 1
                except Exception as e:
                    logger.exception("send_deadline_reminder 1d match %s: %s", match.pk, e)

        if dry_run:
            self.stdout.write(f"Dry-run: матчей за 2 дня: {len(matches_2d)}, за 1 день: {len(matches_1d)}")
        else:
            self.stdout.write(f"Напоминаний отправлено: за 2 дня — {sent_2}, за 1 день — {sent_1}.")
