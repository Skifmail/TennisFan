"""Юнит-тесты: повторная обработка завершённого матча после ввода через админку."""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.utils import timezone

from apps.tournaments.models import Match, TournamentStatus
from tests.support.factories import make_player, make_tournament


class AdminTwoStepCompletionTestCase(TestCase):
    """Сценарий: статус «Завершён» сохранён раньше победителя (типично для админки)."""

    def setUp(self) -> None:
        self.winner = make_player(email_suffix="admin-two-step-win", points=3200.0)
        self.loser = make_player(email_suffix="admin-two-step-lose", points=2800.0)
        self.tournament = make_tournament(
            name="Круговой admin two-step",
            slug="rr-admin-two-step",
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
            player1=self.loser,
            player2=self.winner,
            status=Match.MatchStatus.SCHEDULED,
        )
        self.matches_played_before = {
            self.winner.pk: self.winner.matches_played,
            self.loser.pk: self.loser.matches_played,
        }

    def test_winner_saved_after_status_triggers_rating_and_stats(self) -> None:
        """Второе сохранение с победителем должно пересчитать рейтинг и статистику."""
        self.match.status = Match.MatchStatus.COMPLETED
        self.match.completed_datetime = timezone.now()
        self.match.save()

        self.match.refresh_from_db()
        self.assertEqual(self.match.rating_status, Match.RatingCalcStatus.PENDING)
        self.assertEqual(self.match.rating_delta_player1, 0.0)

        self.match.winner = self.winner
        self.match.player1_set1 = 3
        self.match.player2_set1 = 6
        self.match.player1_set2 = 0
        self.match.player2_set2 = 6
        self.match.save()

        self.match.refresh_from_db()
        self.winner.refresh_from_db()
        self.loser.refresh_from_db()

        self.assertEqual(self.match.rating_status, Match.RatingCalcStatus.CALCULATED)
        self.assertNotEqual(self.match.rating_delta_player1, 0.0)
        self.assertNotEqual(self.match.rating_delta_player2, 0.0)
        self.assertEqual(
            self.winner.matches_played,
            self.matches_played_before[self.winner.pk] + 1,
        )
        self.assertEqual(
            self.loser.matches_played,
            self.matches_played_before[self.loser.pk] + 1,
        )
        self.assertEqual(self.winner.matches_won, 1)
