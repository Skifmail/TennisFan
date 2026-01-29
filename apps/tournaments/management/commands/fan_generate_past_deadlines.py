"""
Сформировать сетку FAN для турниров с истёкшим дедлайном регистрации.

Запуск: python manage.py fan_generate_past_deadlines

Автоматика: проверка также выполняется при посещении главной страницы и списка турниров
(не чаще раза в минуту). Для гарантированного срабатывания добавьте в cron:
  */10 * * * * cd /path/to/project && venv/bin/python manage.py fan_generate_past_deadlines
"""

from django.utils import timezone

from django.core.management.base import BaseCommand

from apps.tournaments.fan import generate_bracket
from apps.tournaments.models import Tournament


class Command(BaseCommand):
    help = (
        "Найти FAN-турниры с истёкшим дедлайном регистрации и не сформированной сеткой, "
        "запустить формирование сетки для каждого."
    )

    def handle(self, *args, **options):
        now = timezone.now()
        qs = list(
            Tournament.objects.filter(
                format="single_elimination",
                bracket_generated=False,
                registration_deadline__lte=now,
                registration_deadline__isnull=False,
            )
        )
        if not qs:
            self.stdout.write("Нет турниров с истёкшим дедлайном регистрации (FAN, сетка ещё не сформирована).")
            return
        total = 0
        for t in qs:
            ok, msg = generate_bracket(t)
            if ok:
                self.stdout.write(self.style.SUCCESS(f"{t.slug}: {msg}"))
                total += 1
            else:
                self.stdout.write(self.style.WARNING(f"{t.slug}: {msg}"))
        if total == 0:
            self.stdout.write("Ни для одного турнира сетка не сформирована (см. предупреждения выше).")
