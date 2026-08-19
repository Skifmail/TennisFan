"""
Обработать просроченные матчи турниров в формате «Олимпийская система».

Walkover (неявка) обоим со штрафом −40; администратор может изменить результат.

Запуск: python manage.py olympic_process_overdue_matches
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.tournaments.models import Match
from apps.tournaments.olympic_consolation import process_overdue_match


class Command(BaseCommand):
    help = (
        "Найти матчи олимпийской системы с истёкшим дедлайном, "
        "проставить Walkover обоим (−40 каждому) и уведомить администраторов."
    )

    def handle(self, *args, **options):
        now = timezone.now()
        matches = list(
            Match.objects.filter(
                tournament__format="olympic_consolation",
                deadline__lte=now,
                deadline__isnull=False,
                status__in=(Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS),
            ).select_related("tournament", "player1", "player2", "team1", "team2")
        )
        if not matches:
            self.stdout.write("Нет просроченных матчей олимпийской системы.")
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
            self.stdout.write("Ни один матч не обработан.")
