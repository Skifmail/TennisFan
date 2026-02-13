"""
Команда для сброса сезонных очков в конце сезона.

Запускается в первый день нового сезона:
- 1 мая (начало летнего сезона)
- 1 октября (начало зимнего сезона)

Выполняет:
1. Определяет завершившийся сезон
2. Сохраняет результаты в SeasonArchive с рангами
3. Обнуляет current_season_points у всех игроков
4. Обновляет season_name и season_year для нового сезона
"""

import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.tournaments.models import SeasonArchive, SeasonPoints
from apps.tournaments.season_utils import get_current_season

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Сброс сезонных очков в конце сезона и архивация результатов"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать что будет сделано без реального выполнения",
        )
        parser.add_argument(
            "--force-season",
            type=str,
            help="Принудительно указать сезон для архивации (например, 'winter_2024')",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        force_season = options.get("force_season")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN MODE - изменения не будут сохранены")
            )

        # Определяем завершившийся сезон
        current_season = get_current_season()

        if force_season:
            # Парсим принудительно указанный сезон
            parts = force_season.split("_")
            if len(parts) != 2:
                self.stdout.write(
                    self.style.ERROR(
                        f"Неверный формат сезона: {force_season}. Используйте 'winter_2024' или 'summer_2025'"
                    )
                )
                return

            season_code, year_str = parts
            try:
                season_year = int(year_str)
            except ValueError:
                self.stdout.write(self.style.ERROR(f"Неверный год: {year_str}"))
                return

            if season_code == "winter":
                ended_season_name = "Зима"
                ended_season_year = season_year
            elif season_code == "summer":
                ended_season_name = "Лето"
                ended_season_year = season_year
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"Неверный код сезона: {season_code}. Используйте 'winter' или 'summer'"
                    )
                )
                return
        else:
            # Автоматически определяем завершившийся сезон
            # Если сейчас начало лета (май), завершилась зима
            # Если сейчас начало зимы (октябрь), завершилось лето
            if current_season.name == "Лето":
                # Завершилась зима предыдущего года
                ended_season_name = "Зима"
                ended_season_year = current_season.year - 1
            else:  # Зима
                # Завершилось лето текущего года
                ended_season_name = "Лето"
                ended_season_year = current_season.year

        self.stdout.write(f"Архивация сезона: {ended_season_name} {ended_season_year}")
        self.stdout.write(
            f"Начало нового сезона: {current_season.name} {current_season.year}"
        )

        # Получаем все записи сезонных очков за завершившийся сезон
        ended_season_points = (
            SeasonPoints.objects.filter(
                season_name=ended_season_name,
                season_year=ended_season_year,
            )
            .select_related("player")
            .order_by("-current_season_points")
        )

        if not ended_season_points.exists():
            self.stdout.write(self.style.WARNING("Нет данных для архивации"))
            return

        # Подсчитываем ранги
        rank = 1
        prev_points = None
        players_to_archive = []

        for sp in ended_season_points:
            if prev_points is not None and sp.current_season_points < prev_points:
                rank = len(players_to_archive) + 1
            prev_points = sp.current_season_points

            players_to_archive.append(
                {
                    "player": sp.player,
                    "points": sp.current_season_points,
                    "rank": rank,
                }
            )

        self.stdout.write(f"Найдено игроков для архивации: {len(players_to_archive)}")

        if not dry_run:
            with transaction.atomic():
                # Сохраняем в архив
                archived_count = 0
                for data in players_to_archive:
                    archive, created = SeasonArchive.objects.update_or_create(
                        player=data["player"],
                        season_name=ended_season_name,
                        season_year=ended_season_year,
                        defaults={
                            "final_points": data["points"],
                            "final_rank": data["rank"],
                        },
                    )
                    if created:
                        archived_count += 1
                    else:
                        self.stdout.write(
                            f"Обновлён архив для {data['player']}: {data['points']} очков, место {data['rank']}"
                        )

                # Обнуляем очки и обновляем сезон для всех игроков
                # Сначала обновляем существующие записи
                updated_count = SeasonPoints.objects.filter(
                    season_name=ended_season_name,
                    season_year=ended_season_year,
                ).update(
                    current_season_points=0,
                    season_name=current_season.name,
                    season_year=current_season.year,
                )

                # Создаём записи для игроков, у которых их нет (на случай если они были созданы после начала сезона)
                from apps.users.models import Player

                all_players = Player.objects.exclude(
                    pk__in=SeasonPoints.objects.filter(
                        season_name=current_season.name,
                        season_year=current_season.year,
                    ).values_list("player_id", flat=True)
                )

                new_records = []
                for player in all_players:
                    new_records.append(
                        SeasonPoints(
                            player=player,
                            current_season_points=0,
                            season_name=current_season.name,
                            season_year=current_season.year,
                        )
                    )

                if new_records:
                    SeasonPoints.objects.bulk_create(new_records, ignore_conflicts=True)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Заархивировано: {archived_count} новых записей, обновлено: {updated_count} записей"
                    )
                )
        else:
            # Dry run - только показываем что будет сделано
            self.stdout.write("\nИгроки для архивации:")
            for i, data in enumerate(players_to_archive[:10], 1):  # Показываем топ-10
                self.stdout.write(
                    f"  {i}. {data['player']}: {data['points']} очков, место {data['rank']}"
                )
            if len(players_to_archive) > 10:
                self.stdout.write(f"  ... и ещё {len(players_to_archive) - 10} игроков")

        self.stdout.write(self.style.SUCCESS("Сброс сезонных очков завершён"))
