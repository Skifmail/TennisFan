"""Откат FAN-рейтинга после ошибочных walkover при снятии участника."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.tournaments.models import Tournament
from apps.tournaments.withdraw import revert_withdrawal_walkover_rating_effects


class Command(BaseCommand):
    """Откатить рейтинг и matches_* у walkover-матчей снятия участников."""

    help = (
        "Откатывает FAN и статистику матчей для WALKOVER без сетов, "
        "созданных при снятии участника (где рейтинг уже был посчитан)."
    )

    def add_arguments(self, parser) -> None:
        """Аргументы CLI."""
        parser.add_argument(
            "--slug",
            type=str,
            default="",
            help="Slug турнира (если пусто — все турниры с такими матчами).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать матчи, без изменений.",
        )

    def handle(self, *args, **options) -> None:
        """Запустить откат."""
        slug = (options.get("slug") or "").strip()
        dry_run = bool(options.get("dry_run"))
        tournament = None
        if slug:
            tournament = Tournament.objects.filter(slug=slug).first()
            if tournament is None:
                self.stderr.write(self.style.ERROR(f"Турнир не найден: {slug}"))
                return

        fixed, messages = revert_withdrawal_walkover_rating_effects(
            tournament=tournament,
            dry_run=dry_run,
        )
        for line in messages:
            self.stdout.write(line)
        prefix = "dry-run: " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}обработано матчей: {fixed}"))
