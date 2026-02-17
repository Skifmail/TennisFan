"""
Отладка расчёта FAN-рейтинга для конкретного матча.

Запуск: python manage.py debug_match_rating 1146
"""

from django.core.management.base import BaseCommand

from apps.tournaments.models import Match
from apps.tournaments.rating import (
    MatchScore,
    PlayerRatingSnapshot,
    actual_score,
    calculate_new_ratings,
    expected_score,
    get_k_factor,
)


class Command(BaseCommand):
    help = "Показать детали расчёта FAN-рейтинга для матча"

    def add_arguments(self, parser):
        parser.add_argument("match_id", type=int, help="ID матча")

    def handle(self, *args, **options):
        pk = options["match_id"]
        match = (
            Match.objects.filter(pk=pk)
            .select_related(
                "player1",
                "player2",
                "winner",
                "team1__player1",
                "team1__player2",
                "team2__player1",
                "team2__player2",
                "winner_team",
            )
            .first()
        )
        if not match:
            self.stdout.write(self.style.ERROR(f"Матч {pk} не найден"))
            return

        is_doubles = bool(match.team1_id and match.team2_id)
        if is_doubles:
            p1 = match.team1.player1
            p2 = match.team2.player1
        else:
            p1 = match.player1
            p2 = match.player2

        if not p1 or not p2:
            self.stdout.write(self.style.ERROR("Нет игроков в матче"))
            return

        # K-factor: матчи ДО этого (matches_played уже +1 после сохранения)
        matches_before_a = max(0, p1.matches_played - 1)
        matches_before_b = max(0, p2.matches_played - 1)
        k_a = get_k_factor(matches_before_a)
        k_b = get_k_factor(matches_before_b)

        rating_a = p1.hidden_rating
        rating_b = p2.hidden_rating

        score_a = MatchScore(
            set1=match.player1_set1 or 0,
            set2=match.player1_set2 or 0,
            set3=match.player1_set3 or 0,
        )
        score_b = MatchScore(
            set1=match.player2_set1 or 0,
            set2=match.player2_set2 or 0,
            set3=match.player2_set3 or 0,
        )
        games_a = score_a.total_games
        games_b = score_b.total_games
        total_games = games_a + games_b

        a_won = (
            match.winner_team_id == match.team1_id
            if match.team1_id and match.team2_id and match.winner_team_id
            else match.winner_id == p1.pk
        )

        e_a = expected_score(rating_a, rating_b)
        e_b = 1.0 - e_a
        s_a = actual_score(games_a, total_games, a_won)
        s_b = actual_score(games_b, total_games, not a_won)

        snap_a = PlayerRatingSnapshot(rating=rating_a, total_matches=matches_before_a)
        snap_b = PlayerRatingSnapshot(rating=rating_b, total_matches=matches_before_b)
        result = calculate_new_ratings(snap_a, snap_b, score_a, score_b, a_won)

        self.stdout.write(self.style.WARNING("=" * 70))
        self.stdout.write(self.style.WARNING(f"Матч #{pk} — расчёт FAN-рейтинга"))
        self.stdout.write(self.style.WARNING("=" * 70))
        self.stdout.write("")
        self.stdout.write("Счёт:")
        self.stdout.write(
            f"  P1: {match.player1_set1}-{match.player2_set1}, "
            f"{match.player1_set2}-{match.player2_set2}, "
            f"{match.player1_set3 or '—'}-{match.player2_set3 or '—'}"
        )
        self.stdout.write(f"  Игры: P1={games_a}, P2={games_b}, всего={total_games}")
        self.stdout.write(f"  Победитель: {'P1' if a_won else 'P2'}")
        self.stdout.write("")
        self.stdout.write("Рейтинги ДО матча:")
        self.stdout.write(
            f"  P1 ({p1}): {rating_a:.1f}, матчей={matches_before_a}, K={k_a}"
        )
        self.stdout.write(
            f"  P2 ({p2}): {rating_b:.1f}, матчей={matches_before_b}, K={k_b}"
        )
        self.stdout.write("")
        self.stdout.write("Expected (E) и Actual (S):")
        self.stdout.write(f"  E_A={e_a:.4f}, E_B={e_b:.4f}")
        self.stdout.write(f"  S_A={s_a:.4f}, S_B={s_b:.4f}")
        self.stdout.write("")
        self.stdout.write("Формула: delta = K * (S - E)")
        self.stdout.write(
            f"  delta_A = {k_a} * ({s_a:.4f} - {e_a:.4f}) = {result.delta_a:.1f}"
        )
        self.stdout.write(
            f"  delta_B = {k_b} * ({s_b:.4f} - {e_b:.4f}) = {result.delta_b:.1f}"
        )
        self.stdout.write("")
        self.stdout.write("Результат:")
        self.stdout.write(
            f"  P1: {rating_a:.1f} → {result.new_rating_a:.1f} (Δ {result.delta_a:+.1f})"
        )
        self.stdout.write(
            f"  P2: {rating_b:.1f} → {result.new_rating_b:.1f} (Δ {result.delta_b:+.1f})"
        )
        self.stdout.write("")
        self.stdout.write("Сохранённые в Match:")
        self.stdout.write(f"  rating_delta_player1={match.rating_delta_player1}")
        self.stdout.write(f"  rating_delta_player2={match.rating_delta_player2}")
        self.stdout.write("")

        if total_games == 0:
            self.stdout.write(
                self.style.ERROR(
                    "⚠️ total_games=0! При нулевом счёте actual_score=0 для обоих, "
                    "оба получают отрицательную дельту (S < E). Нужно исправить логику."
                )
            )
