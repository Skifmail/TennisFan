"""Откат авто-FAN walkover по просрочке и простановка Walkover за неявку.

Запуск на сервере (контейнер web):

    python manage.py replace_overdue_walkover \\
        --player Кормилина --opponent Сорокина --loser Кормилина --dry-run

    python manage.py replace_overdue_walkover \\
        --player Кормилина --opponent Сорокина --loser Кормилина
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.tournaments.overdue import (
    find_match_for_walkover_replace,
    replace_no_show_walkover,
    resolve_walkover_loser,
)


class Command(BaseCommand):
    """Откатить ошибочный FAN-walkover и проставить неявку (−40 / 0)."""

    help = (
        "Откатывает FAN-дельты авто-Walkover без счёта и заново ставит "
        "Walkover за неявку: −40 неявившемуся, 0 сопернику. "
        "Участникам письма не отправляются, если не указан --notify."
    )

    def add_arguments(self, parser) -> None:
        """Аргументы CLI."""
        parser.add_argument(
            "--match-id",
            type=int,
            default=0,
            help="ID матча (если известен).",
        )
        parser.add_argument(
            "--player",
            type=str,
            default="",
            help="Фрагмент имени/фамилии первого игрока.",
        )
        parser.add_argument(
            "--opponent",
            type=str,
            default="",
            help="Фрагмент имени/фамилии второго игрока.",
        )
        parser.add_argument(
            "--tournament",
            type=str,
            default="",
            help="Фрагмент названия или slug турнира.",
        )
        parser.add_argument(
            "--loser",
            type=str,
            default="",
            help="Игрок, которому засчитывается неявка (−40).",
        )
        parser.add_argument(
            "--keep-winner",
            action="store_true",
            help="Оставить текущего победителя, неявку — сопернику.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать откат и новый рейтинг, не писать в БД.",
        )
        parser.add_argument(
            "--notify",
            action="store_true",
            help="Уведомить участников о результате (по умолчанию нет).",
        )

    def handle(self, *args, **options) -> None:
        """Найти матч, откатить FAN и проставить Walkover за неявку."""
        match_id = int(options.get("match_id") or 0) or None
        player = str(options.get("player") or "")
        opponent = str(options.get("opponent") or "")
        tournament = str(options.get("tournament") or "")
        loser_query = str(options.get("loser") or "")
        keep_winner = bool(options.get("keep_winner"))
        dry_run = bool(options.get("dry_run"))
        notify = bool(options.get("notify"))

        if not keep_winner and not loser_query.strip():
            raise CommandError("Укажите --loser или --keep-winner.")
        if keep_winner and loser_query.strip():
            raise CommandError("Укажите либо --loser, либо --keep-winner.")

        try:
            match = find_match_for_walkover_replace(
                match_id=match_id,
                player_query=player,
                opponent_query=opponent,
                tournament_query=tournament,
            )
            loser = resolve_walkover_loser(
                match,
                loser_query=loser_query,
                keep_winner=keep_winner,
            )
            lines = replace_no_show_walkover(
                match,
                loser=loser,
                dry_run=dry_run,
                notify=notify,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        for line in lines:
            self.stdout.write(line)
        prefix = "dry-run: " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}готово, матч {match.pk}"))
