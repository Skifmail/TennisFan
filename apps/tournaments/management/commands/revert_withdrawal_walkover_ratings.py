"""Откат FAN-рейтинга после ошибочных walkover при снятии участника."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.tournaments.models import Tournament
from apps.tournaments.withdraw import revert_withdrawal_walkover_rating_effects
from apps.users.models import Player
from apps.users.rating_utils import rating_to_ntrp_level, rating_to_skill_level


class Command(BaseCommand):
    """Откатить рейтинг и matches_* у walkover-матчей снятия участников."""

    help = (
        "Откатывает FAN и статистику матчей для WALKOVER без сетов, "
        "созданных при снятии участника (где рейтинг уже был посчитан). "
        "Если дельты уже обнулены — используйте --set-rating."
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
        parser.add_argument(
            "--player-id",
            type=int,
            default=0,
            help="ID игрока для ручной установки рейтинга (--set-rating).",
        )
        parser.add_argument(
            "--set-rating",
            type=float,
            default=None,
            help=(
                "Вручную выставить total_points/hidden_rating игроку "
                "(когда дельты матчей уже обнулены). Требует --player-id."
            ),
        )

    def handle(self, *args, **options) -> None:
        """Запустить откат или ручную установку рейтинга."""
        set_rating = options.get("set_rating")
        player_id = int(options.get("player_id") or 0)
        if set_rating is not None:
            if not player_id:
                self.stderr.write(
                    self.style.ERROR("Для --set-rating укажите --player-id.")
                )
                return
            player = Player.objects.filter(pk=player_id).first()
            if player is None:
                self.stderr.write(self.style.ERROR(f"Игрок не найден: id={player_id}"))
                return
            if options.get("dry_run"):
                self.stdout.write(
                    f"[dry-run] player={player_id} "
                    f"{player} rating {player.total_points} → {set_rating}"
                )
                return
            new_rating = max(0.0, float(set_rating))
            old = float(player.total_points)
            player.hidden_rating = new_rating
            player.total_points = new_rating
            player.skill_level = rating_to_skill_level(new_rating)
            player.ntrp_level = rating_to_ntrp_level(new_rating)
            player.save(
                update_fields=[
                    "hidden_rating",
                    "total_points",
                    "skill_level",
                    "ntrp_level",
                ]
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"player={player_id} {player}: rating {old} → {new_rating}"
                )
            )
            return

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
