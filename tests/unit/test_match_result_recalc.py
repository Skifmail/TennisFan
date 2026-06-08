"""Тесты пересчёта рейтинга при редактировании завершённого матча."""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from apps.tournaments.admin import MatchAdminForm
from apps.tournaments.models import Match, TournamentStatus
from tests.support.factories import make_player, make_tournament


class MatchResultRecalcTestCase(TestCase):
    """Пересчёт FAN и статистики при правке результата в админке."""

    def setUp(self) -> None:
        self.player1 = make_player(email_suffix="recalc-p1", points=3000.0)
        self.player2 = make_player(email_suffix="recalc-p2", points=2800.0)
        self.tournament = make_tournament(
            name="Круговой recalc",
            slug="rr-recalc",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            bracket_generated=True,
            start_date=date.today(),
        )
        self.match = Match.objects.create(
            tournament=self.tournament,
            round_name="Тур 1",
            round_index=1,
            round_order=1,
            player1=self.player1,
            player2=self.player2,
            status=Match.MatchStatus.SCHEDULED,
        )

    def _save_result(
        self,
        *,
        winner_side: str,
        p1s1: int,
        p2s1: int,
        p1s2: int,
        p2s2: int,
    ) -> Match:
        form = MatchAdminForm(
            data={
                "tournament": self.tournament.pk,
                "round_name": "Тур 1",
                "deadline": "",
                "player1": self.player1.pk,
                "player2": self.player2.pk,
                "team1": "",
                "team2": "",
                "winner_side": winner_side,
                "player1_set1": p1s1,
                "player2_set1": p2s1,
                "player1_set2": p1s2,
                "player2_set2": p2s2,
                "player1_set3": "",
                "player2_set3": "",
                "status": self.match.status,
                "completed_datetime": self.match.completed_datetime or "",
                "court": "",
                "round_index": 1,
                "round_order": 1,
                "is_consolation": False,
                "tvd_group": "",
                "tvd_stage": "",
                "next_match": "",
                "loser_next_match": "",
                "placement_min": "",
                "placement_max": "",
                "scheduled_datetime": "",
                "points_player1": self.match.points_player1,
                "points_player2": self.match.points_player2,
                "match_type": Match.MatchType.TOURNAMENT,
                "rating_status": self.match.rating_status,
                "rating_delta_player1": self.match.rating_delta_player1,
                "rating_delta_player2": self.match.rating_delta_player2,
            },
            instance=self.match,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.match = Match.objects.get(pk=saved.pk)
        return self.match

    def test_edit_winner_recalculates_rating_and_matches_won(self) -> None:
        """Смена победителя пересчитывает рейтинг и matches_won без дубля matches_played."""
        self._save_result(winner_side="player2", p1s1=3, p2s1=6, p1s2=0, p2s2=6)

        self.player1.refresh_from_db()
        self.player2.refresh_from_db()
        rating_after_first = float(self.player2.total_points)
        self.assertEqual(self.player1.matches_played, 1)
        self.assertEqual(self.player2.matches_played, 1)
        self.assertEqual(self.player1.matches_won, 0)
        self.assertEqual(self.player2.matches_won, 1)

        self._save_result(winner_side="player1", p1s1=6, p2s1=3, p1s2=6, p2s2=0)

        self.player1.refresh_from_db()
        self.player2.refresh_from_db()
        self.match.refresh_from_db()

        self.assertEqual(self.player1.matches_played, 1)
        self.assertEqual(self.player2.matches_played, 1)
        self.assertEqual(self.player1.matches_won, 1)
        self.assertEqual(self.player2.matches_won, 0)
        self.assertNotAlmostEqual(float(self.player1.total_points), 3000.0, delta=0.01)
        self.assertNotAlmostEqual(
            float(self.player2.total_points), rating_after_first, delta=0.01
        )
        self.assertEqual(self.match.rating_status, Match.RatingCalcStatus.CALCULATED)
        self.assertEqual(self.match.winner_id, self.player1.pk)

    def test_edit_score_only_recalculates_rating(self) -> None:
        """Изменение только счёта при том же победителе обновляет дельту рейтинга."""
        self._save_result(winner_side="player2", p1s1=3, p2s1=6, p1s2=0, p2s2=6)
        self.player2.refresh_from_db()
        first_rating = float(self.player2.total_points)
        first_delta = self.match.rating_delta_player2

        self._save_result(winner_side="player2", p1s1=0, p2s1=6, p1s2=0, p2s2=6)

        self.player2.refresh_from_db()
        self.match.refresh_from_db()

        self.assertEqual(self.player2.matches_won, 1)
        self.assertNotEqual(self.match.rating_delta_player2, first_delta)
        self.assertNotAlmostEqual(
            float(self.player2.total_points), first_rating, delta=0.01
        )
