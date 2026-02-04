"""
Сформировать основную сетку турнира в формате «Олимпийская система (утешительная сетка)».

Запуск: python manage.py generate_olympic_bracket <slug>
"""

from django.core.management.base import BaseCommand

from apps.tournaments.models import Tournament
from apps.tournaments.olympic_consolation import generate_bracket


class Command(BaseCommand):
    help = (
        "Сформировать основную сетку олимпийской системы (посев по рейтингу). "
        "Утешительные сетки создаются по мере завершения раундов."
    )

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
