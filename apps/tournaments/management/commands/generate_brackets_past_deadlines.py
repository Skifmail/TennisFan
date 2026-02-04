"""
Сформировать сетку для турниров (FAN, Олимпийская, Круговой) с истёкшим дедлайном регистрации.

Запуск: python manage.py generate_brackets_past_deadlines

Вызывает общую логику формирования сетки для всех форматов. Рекомендуется добавить в cron:
  */10 * * * * cd /path/to/project && venv/bin/python manage.py generate_brackets_past_deadlines
"""

from django.core.management.base import BaseCommand

from apps.tournaments.fan import check_and_generate_past_deadline_brackets


class Command(BaseCommand):
    help = (
        "Найти турниры (FAN, Олимпийская система, Круговой) с истёкшим дедлайном регистрации "
        "и не сформированной сеткой, сформировать сетку для каждого."
    )

    def handle(self, *args, **options):
        count = check_and_generate_past_deadline_brackets()
        if count == 0:
            self.stdout.write("Нет турниров с истёкшим дедлайном регистрации (сетка уже сформирована или дедлайн не наступил).")
        else:
            self.stdout.write(self.style.SUCCESS(f"Сформировано сеток: {count}."))
