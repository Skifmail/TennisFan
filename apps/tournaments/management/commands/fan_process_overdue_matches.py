"""
Уведомить администраторов о просроченных FAN- и ТВД-матчах (дедлайн истёк, матч не сыгран).

Запуск: python manage.py fan_process_overdue_matches
"""

from django.core.management.base import BaseCommand

from apps.tournaments.overdue import notify_overdue_matches_for_formats


class Command(BaseCommand):
    help = (
        "Найти FAN-матчи и матчи ТВД с истёкшим дедлайном "
        "и уведомить администраторов. Результат автоматически не проставляется."
    )

    def handle(self, *args, **options):
        notified, skipped = notify_overdue_matches_for_formats(
            ["single_elimination", "weekend_day"]
        )
        if notified == 0 and skipped == 0:
            self.stdout.write("Нет просроченных FAN/ТВД-матчей.")
            return
        self.stdout.write(
            self.style.SUCCESS(f"Уведомлено матчей: {notified}. Пропущено: {skipped}.")
        )
