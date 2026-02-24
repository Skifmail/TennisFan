"""
Management command: publish monthly skill levels.

Run on the 1st of each month (via cron or scheduler).
Updates skill_level based on current total_points (which is updated after each match).

Note: total_points is now updated immediately after each match for user visibility.
This command only updates skill_level based on the current rating.

Usage:
    python manage.py monthly_rating_publish
    python manage.py monthly_rating_publish --dry-run
"""

import logging
from typing import Any

from django.core.management.base import BaseCommand

from apps.users.models import Player
from apps.users.rating_utils import rating_to_skill_level

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Update monthly skill levels: update skill_level based on current total_points "
        "for every active player. total_points is updated after each match."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Show what would change without writing to the database.",
        )

    def handle(self, *args: Any, **options: Any) -> str | None:
        dry_run: bool = options["dry_run"]
        players = Player.objects.filter(is_bye=False).only(
            "pk",
            "total_points",
            "skill_level",
        )

        updated = 0
        skipped = 0
        for player in players.iterator(chunk_size=500):
            # skill_level обновляется на основе текущего total_points
            # (который уже обновлён после каждого матча)
            new_skill_level = rating_to_skill_level(player.total_points)

            if new_skill_level == player.skill_level:
                skipped += 1
                continue

            old_skill_level = player.skill_level

            if dry_run:
                self.stdout.write(
                    f"  [DRY-RUN] Player {player.pk}: "
                    f"skill_level: {old_skill_level} → {new_skill_level} "
                    f"(rating: {player.total_points})"
                )
            else:
                player.skill_level = new_skill_level
                player.save(update_fields=["skill_level"])
                logger.info(
                    "Skill level updated: Player %s: %s → %s (rating: %.1f)",
                    player.pk,
                    old_skill_level,
                    new_skill_level,
                    player.total_points,
                )

            updated += 1

        summary = (
            f"{'[DRY-RUN] ' if dry_run else ''}"
            f"Monthly skill level update: {updated} updated, {skipped} unchanged."
        )
        self.stdout.write(self.style.SUCCESS(summary))
        logger.info(summary)
        return summary
