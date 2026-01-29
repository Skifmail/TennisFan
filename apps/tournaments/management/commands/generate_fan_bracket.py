"""
Сформировать сетку FAN для турнира.

Запуск: python manage.py generate_fan_bracket <slug>
"""

from django.core.management.base import BaseCommand

from apps.tournaments.fan import generate_bracket
from apps.tournaments.models import Tournament


class Command(BaseCommand):
    help = "Сформировать сетку FAN (посев по рейтингу). Турнир: format=FAN, 2 ≤ участников ≤ max."

    def add_arguments(self, parser):
        parser.add_argument("slug", type=str, help="Slug турнира")

    def handle(self, *args, **options):
        slug = options["slug"]
        t = Tournament.objects.filter(slug=slug).first()
        if not t:
            self.stdout.write(self.style.ERROR(f"Турнир не найден: {slug}"))
            return
        ok, msg = generate_bracket(t)
        if ok:
            self.stdout.write(self.style.SUCCESS(msg))
        else:
            self.stdout.write(self.style.ERROR(msg))
