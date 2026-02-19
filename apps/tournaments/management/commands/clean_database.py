"""
Команда для полной очистки БД от всех турниров, матчей, результатов и уведомлений.
Обновляет рейтинг существующих игроков на основе их текущего уровня силы.
"""

import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.sparring.models import SparringRequest, SparringResponse
from apps.tournaments.models import (
    DeadlineExtensionRequest,
    HeadToHead,
    Match,
    MatchResultProposal,
    SeasonArchive,
    SeasonPoints,
    SeasonRating,
    Tournament,
    TournamentAllowedCategory,
    TournamentPlayerResult,
    TournamentTeam,
)
from apps.users.models import Notification, Player
from apps.users.rating_utils import get_starting_points

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Очистить БД от всех турниров, матчей, результатов и обновить рейтинг игроков"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать что будет сделано без реального выполнения",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)

        if dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN MODE - изменения не будут сохранены")
            )

        # Подсчитываем количество записей для удаления
        counts = {
            "matches": Match.objects.count(),
            "tournaments": Tournament.objects.count(),
            "tournament_teams": TournamentTeam.objects.count(),
            "tournament_results": TournamentPlayerResult.objects.count(),
            "match_proposals": MatchResultProposal.objects.count(),
            "deadline_requests": DeadlineExtensionRequest.objects.count(),
            "head_to_head": HeadToHead.objects.count(),
            "season_ratings": SeasonRating.objects.count(),
            "season_points": SeasonPoints.objects.count(),
            "season_archives": SeasonArchive.objects.count(),
            "notifications": Notification.objects.count(),
            "sparring_requests": SparringRequest.objects.count(),
            "sparring_responses": SparringResponse.objects.count(),
            "tournament_categories": TournamentAllowedCategory.objects.count(),
        }

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.WARNING("СТАТИСТИКА ПЕРЕД ОЧИСТКОЙ:"))
        self.stdout.write("=" * 60)
        for name, count in counts.items():
            self.stdout.write(f"  {name}: {count}")

        players_count = Player.objects.exclude(is_bye=True).count()
        self.stdout.write(f"\n  Игроков (не bye): {players_count}")

        if not dry_run:
            # Запрашиваем подтверждение
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write(
                self.style.ERROR(
                    "ВНИМАНИЕ: Это действие удалит ВСЕ данные о турнирах, матчах и результатах!"
                )
            )
            self.stdout.write("=" * 60)
            confirm = input("Введите 'yes' для подтверждения: ")

            if confirm.lower() != "yes":
                self.stdout.write(self.style.ERROR("Операция отменена"))
                return

        with transaction.atomic():
            if dry_run:
                self.stdout.write(
                    "\n" + self.style.WARNING("DRY RUN - операции не выполняются")
                )
                return

            self.stdout.write("\n" + "=" * 60)
            self.stdout.write("НАЧАЛО ОЧИСТКИ БД...")
            self.stdout.write("=" * 60)

            # 1. Удаляем матчи (это удалит связанные данные через CASCADE)
            self.stdout.write("\n1. Удаление матчей...")
            matches_deleted = Match.objects.all().delete()[0]
            self.stdout.write(f"   Удалено матчей: {matches_deleted}")

            # 2. Удаляем предложения результатов матчей (если остались)
            self.stdout.write("\n2. Удаление предложений результатов...")
            proposals_deleted = MatchResultProposal.objects.all().delete()[0]
            self.stdout.write(f"   Удалено предложений: {proposals_deleted}")

            # 3. Удаляем команды турниров
            self.stdout.write("\n3. Удаление команд турниров...")
            teams_deleted = TournamentTeam.objects.all().delete()[0]
            self.stdout.write(f"   Удалено команд: {teams_deleted}")

            # 4. Удаляем результаты игроков в турнирах
            self.stdout.write("\n4. Удаление результатов игроков в турнирах...")
            results_deleted = TournamentPlayerResult.objects.all().delete()[0]
            self.stdout.write(f"   Удалено результатов: {results_deleted}")

            # 5. Удаляем запросы на продление дедлайна
            self.stdout.write("\n5. Удаление запросов на продление дедлайна...")
            deadline_deleted = DeadlineExtensionRequest.objects.all().delete()[0]
            self.stdout.write(f"   Удалено запросов: {deadline_deleted}")

            # 6. Удаляем статистику встреч
            self.stdout.write("\n6. Удаление статистики встреч...")
            h2h_deleted = HeadToHead.objects.all().delete()[0]
            self.stdout.write(f"   Удалено записей: {h2h_deleted}")

            # 7. Удаляем разрешенные категории турниров
            self.stdout.write("\n7. Удаление разрешенных категорий турниров...")
            categories_deleted = TournamentAllowedCategory.objects.all().delete()[0]
            self.stdout.write(f"   Удалено категорий: {categories_deleted}")

            # 8. Удаляем турниры
            self.stdout.write("\n8. Удаление турниров...")
            tournaments_deleted = Tournament.objects.all().delete()[0]
            self.stdout.write(f"   Удалено турниров: {tournaments_deleted}")

            # 9. Удаляем сезонные рейтинги
            self.stdout.write("\n9. Удаление сезонных рейтингов...")
            season_ratings_deleted = SeasonRating.objects.all().delete()[0]
            self.stdout.write(f"   Удалено рейтингов: {season_ratings_deleted}")

            # 10. Удаляем сезонные очки
            self.stdout.write("\n10. Удаление сезонных очков...")
            season_points_deleted = SeasonPoints.objects.all().delete()[0]
            self.stdout.write(f"   Удалено записей: {season_points_deleted}")

            # 11. Удаляем архивы сезонов
            self.stdout.write("\n11. Удаление архивов сезонов...")
            archives_deleted = SeasonArchive.objects.all().delete()[0]
            self.stdout.write(f"   Удалено архивов: {archives_deleted}")

            # 12. Удаляем уведомления
            self.stdout.write("\n12. Удаление уведомлений...")
            notifications_deleted = Notification.objects.all().delete()[0]
            self.stdout.write(f"   Удалено уведомлений: {notifications_deleted}")

            # 13. Удаляем спарринги
            self.stdout.write("\n13. Удаление спаррингов...")
            sparring_responses_deleted = SparringResponse.objects.all().delete()[0]
            sparring_requests_deleted = SparringRequest.objects.all().delete()[0]
            self.stdout.write(
                f"   Удалено заявок на спарринг: {sparring_requests_deleted}"
            )
            self.stdout.write(
                f"   Удалено откликов на спарринг: {sparring_responses_deleted}"
            )

            # 14. Обновляем статистику игроков и рейтинг
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write("ОБНОВЛЕНИЕ ИГРОКОВ...")
            self.stdout.write("=" * 60)

            players = Player.objects.exclude(is_bye=True).select_related("user")
            updated_count = 0
            skipped_count = 0

            for player in players:
                try:
                    # Получаем текущий уровень силы
                    ntrp_level = player.ntrp_level
                    if not ntrp_level or ntrp_level == 0:
                        self.stdout.write(
                            self.style.WARNING(
                                f"   Пропущен игрок {player.pk} ({player}): нет уровня силы"
                            )
                        )
                        skipped_count += 1
                        continue

                    # Вычисляем новый рейтинг на основе уровня силы
                    if isinstance(ntrp_level, (int, float)):
                        ntrp_decimal = Decimal(str(ntrp_level))
                    else:
                        ntrp_decimal = ntrp_level

                    new_points = get_starting_points(ntrp_decimal)

                    # Обновляем игрока
                    player.matches_played = 0
                    player.matches_won = 0
                    player.total_points = float(new_points)
                    player.hidden_rating = float(new_points)
                    player.save(
                        update_fields=[
                            "matches_played",
                            "matches_won",
                            "total_points",
                            "hidden_rating",
                        ]
                    )

                    updated_count += 1
                    if updated_count % 10 == 0:
                        self.stdout.write(f"   Обновлено игроков: {updated_count}...")

                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(
                            f"   Ошибка при обновлении игрока {player.pk} ({player}): {e}"
                        )
                    )
                    logger.error(
                        f"Ошибка при обновлении игрока {player.pk}: {e}", exc_info=True
                    )
                    skipped_count += 1

            self.stdout.write(f"\n   Всего обновлено игроков: {updated_count}")
            if skipped_count > 0:
                self.stdout.write(
                    self.style.WARNING(f"   Пропущено игроков: {skipped_count}")
                )

            self.stdout.write("\n" + "=" * 60)
            self.stdout.write(self.style.SUCCESS("ОЧИСТКА БД ЗАВЕРШЕНА УСПЕШНО!"))
            self.stdout.write("=" * 60)

            # Финальная статистика
            self.stdout.write("\nФИНАЛЬНАЯ СТАТИСТИКА:")
            self.stdout.write(f"  Матчей: {Match.objects.count()}")
            self.stdout.write(f"  Турниров: {Tournament.objects.count()}")
            self.stdout.write(f"  Уведомлений: {Notification.objects.count()}")
            self.stdout.write(f"  Игроков обновлено: {updated_count}")
