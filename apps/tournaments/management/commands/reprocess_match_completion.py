"""
Пересчитать статистику и FAN-рейтинг для завершённых матчей, пропущенных сигналом.

Типичный случай: в админке сначала сохранили статус «Завершён» без победителя,
затем добавили победителя отдельным сохранением.

Запуск:
    python manage.py reprocess_match_completion 4
    python manage.py reprocess_match_completion --all-pending
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.tournaments.models import Match
from apps.tournaments.signals import update_player_stats


class Command(BaseCommand):
    """Пересчитать статистику и FAN-рейтинг для пропущенных матчей."""

    help = (
        "Пересчитать статистику и FAN-рейтинг для завершённых матчей "
        "без статуса calculated"
    )

    def add_arguments(self, parser) -> None:
        """Добавить аргументы команды.

        Args:
            parser: Парсер аргументов Django.

        Returns:
            None
        """
        parser.add_argument(
            "match_ids",
            nargs="*",
            type=int,
            help="ID матчей для пересчёта",
        )
        parser.add_argument(
            "--all-pending",
            action="store_true",
            help="Обработать все завершённые матчи без rating_status=calculated",
        )

    def handle(self, *args, **options) -> None:
        """Выполнить пересчёт для указанных или всех пропущенных матчей.

        Args:
            *args: Позиционные аргументы Django.
            **options: Опции команды.

        Returns:
            None
        """
        match_ids: list[int] = options["match_ids"]
        all_pending: bool = options["all_pending"]

        if not match_ids and not all_pending:
            self.stdout.write(
                self.style.ERROR(
                    "Укажите ID матча или флаг --all-pending",
                )
            )
            return

        qs = (
            Match.objects.filter(
                status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER],
            )
            .exclude(
                Q(winner__isnull=True) & Q(winner_team__isnull=True),
            )
            .exclude(
                rating_status=Match.RatingCalcStatus.CALCULATED,
            )
        )

        if match_ids:
            qs = qs.filter(pk__in=match_ids)

        matches = list(
            qs.select_related(
                "player1",
                "player2",
                "winner",
                "tournament",
            ).order_by("pk")
        )

        if not matches:
            self.stdout.write(self.style.WARNING("Нет матчей для пересчёта"))
            return

        processed = 0
        for match in matches:
            match._old_status = match.status
            update_player_stats(Match, match, created=False)
            match.refresh_from_db()
            processed += 1
            self.stdout.write(
                f"Матч #{match.pk}: rating_status={match.rating_status}, "
                f"Δ1={match.rating_delta_player1}, Δ2={match.rating_delta_player2}"
            )

        self.stdout.write(
            self.style.SUCCESS(f"Пересчитано матчей: {processed}"),
        )
