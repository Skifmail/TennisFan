"""
Обработать просроченные матчи круговых турниров (дедлайн истёк, матч не сыгран).

Walkover (неявка) обоим со штрафом −40; администратор может изменить результат.

Запуск: python manage.py round_robin_process_overdue_matches

Рекомендуется добавить в cron (например, раз в день после полуночи или каждые 6–12 часов):
  0 0 * * * cd /path/to/project && venv/bin/python manage.py round_robin_process_overdue_matches
  0 */6 * * * ...
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.tournaments.models import Match
from apps.tournaments.round_robin import process_overdue_match


class Command(BaseCommand):
    help = (
        "Найти матчи круговых турниров с истёкшим дедлайном, "
        "проставить Walkover обоим (−40 каждому) и уведомить администраторов."
    )

    def handle(self, *args, **options):
        now = timezone.now()
        matches = list(
            Match.objects.filter(
                tournament__format="round_robin",
                deadline__lte=now,
                deadline__isnull=False,
                status__in=(Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS),
            ).select_related("tournament", "player1", "player2", "team1", "team2")
        )
        if not matches:
            self.stdout.write("Нет просроченных матчей круговых турниров.")
            return
        total = 0
        for m in matches:
            ok, msg = process_overdue_match(m)
            if ok:
                self.stdout.write(self.style.SUCCESS(msg))
                total += 1
            else:
                self.stdout.write(self.style.WARNING(f"Матч {m.pk}: {msg}"))
        if total == 0:
            self.stdout.write("Ни один матч не обработан (см. предупреждения выше).")
        else:
            self.stdout.write(self.style.SUCCESS(f"Обработано матчей: {total}"))
