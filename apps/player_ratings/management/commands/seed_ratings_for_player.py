"""
Добавить 20 тестовых оценок по всем 12 метрикам для указанного игрока (по умолчанию Леонид Ермолаев).
Создаёт 20 завершённых спарринг-матчей и по одной оценке от соперника в каждый матч.
"""

import random

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.player_ratings.enums import SkillMetric
from apps.player_ratings.models import PlayerSkillRating
from apps.player_ratings.services import recalc_aggregates_for_player
from apps.tournaments.models import Match
from apps.users.models import Player


class Command(BaseCommand):
    help = "Добавить 20 имитированных оценок по всем параметрам для игрока (по умолчанию Леонид Ермолаев)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--player-id",
            type=int,
            default=None,
            help="ID игрока (Player). Если не указан, ищем по имени Леонид Ермолаев.",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=20,
            help="Количество оценок (и матчей) для создания (по умолчанию 20).",
        )

    def handle(self, *args, **options):
        player_id = options["player_id"]
        count = max(1, min(options["count"], 100))

        if player_id:
            try:
                target_player = Player.objects.get(pk=player_id)
            except Player.DoesNotExist:
                self.stderr.write(
                    self.style.ERROR(f"Игрок с id={player_id} не найден.")
                )
                return
        else:
            target_player = (
                Player.objects.filter(
                    user__first_name__icontains="Леонид",
                    user__last_name__icontains="Ермолаев",
                )
                .select_related("user")
                .first()
            )
            if not target_player:
                target_player = Player.objects.select_related("user").first()
                if not target_player:
                    self.stderr.write(self.style.ERROR("В БД нет ни одного игрока."))
                    return
                self.stdout.write(
                    self.style.WARNING(
                        f"Леонид Ермолаев не найден. Используется первый игрок: {target_player} (id={target_player.pk})."
                    )
                )
            else:
                self.stdout.write(
                    f"Найден игрок: {target_player} (id={target_player.pk})"
                )

        other_players = list(
            Player.objects.exclude(pk=target_player.pk).exclude(user__isnull=True)[
                : count + 50
            ]
        )
        if len(other_players) < count:
            self.stderr.write(
                self.style.ERROR(
                    f"В БД только {len(other_players)} других игроков. Нужно минимум {count}."
                )
            )
            return

        metric_names = SkillMetric.all_metric_names()
        random.seed(42)

        with transaction.atomic():
            created_ratings = 0
            used_opponents = random.sample(other_players, count)
            now = timezone.now()

            for i, opponent in enumerate(used_opponents):
                match = Match.objects.create(
                    tournament=None,
                    match_type=Match.MatchType.SPARRING,
                    player1=target_player,
                    player2=opponent,
                    winner=target_player if i % 2 == 0 else opponent,
                    status=Match.MatchStatus.COMPLETED,
                    player1_set1=6,
                    player2_set1=4,
                    player1_set2=6,
                    player2_set2=3,
                    scheduled_datetime=now,
                    completed_datetime=now,
                )
                rating = PlayerSkillRating(
                    match=match,
                    from_player=opponent,
                    to_player=target_player,
                )
                for name in metric_names:
                    setattr(rating, name, random.randint(1, 10))
                rating.save()
                created_ratings += 1

            recalc_aggregates_for_player(target_player, metric_names)

        self.stdout.write(
            self.style.SUCCESS(
                f"Создано {created_ratings} матчей и {created_ratings} оценок для {target_player}. "
                f"Агрегаты пересчитаны. Профиль: /users/profile/{target_player.pk}/"
            )
        )
