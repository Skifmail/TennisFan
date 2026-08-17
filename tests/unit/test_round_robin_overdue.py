"""Юнит-тесты: просроченные матчи кругового турнира (уведомление администратора)."""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.tournaments.models import Match, TournamentStatus
from apps.tournaments.round_robin import (
    _overdue_winner_round_robin,
    process_overdue_match,
)
from tests.support.factories import make_player, make_tournament


class OverdueWinnerRoundRobinTestCase(TestCase):
    """Выбор победителя при просрочке дедлайна в круговом турнире."""

    def setUp(self) -> None:
        self.strong = make_player(email_suffix="rr-overdue-strong", points=3400.0)
        self.weak = make_player(email_suffix="rr-overdue-weak", points=2400.0)
        self.tournament = make_tournament(
            name="Круговой просрочка",
            slug="rr-overdue-winner",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            bracket_generated=True,
            start_date=date.today(),
        )

    def _scheduled_match(self) -> Match:
        return Match.objects.create(
            tournament=self.tournament,
            round_name="Тур 1",
            round_index=1,
            round_order=1,
            player1=self.weak,
            player2=self.strong,
            status=Match.MatchStatus.SCHEDULED,
            deadline=timezone.now() - timedelta(hours=2),
        )

    def test_overdue_winner_picks_higher_rating(self) -> None:
        match = self._scheduled_match()

        winner = _overdue_winner_round_robin(match)

        self.assertEqual(winner, self.strong)

    def test_overdue_winner_picks_lower_id_on_equal_rating(self) -> None:
        self.strong.total_points = 3000.0
        self.strong.save(update_fields=["total_points"])
        self.weak.total_points = 3000.0
        self.weak.save(update_fields=["total_points"])
        match = self._scheduled_match()
        expected = self.weak if self.weak.pk < self.strong.pk else self.strong

        winner = _overdue_winner_round_robin(match)

        self.assertEqual(winner, expected)


class ProcessOverdueRoundRobinMatchTestCase(TestCase):
    """Обработка просроченного матча: уведомление админа, матч не закрывается."""

    def setUp(self) -> None:
        self.strong = make_player(
            email_suffix="rr-process-strong",
            points=3400.0,
        )
        self.weak = make_player(
            email_suffix="rr-process-weak",
            points=2400.0,
        )
        for player in (self.strong, self.weak):
            player.matches_played = 15
            player.save(update_fields=["matches_played"])
        self.tournament = make_tournament(
            name="Круговой просрочка process",
            slug="rr-overdue-process",
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
            player1=self.strong,
            player2=self.weak,
            status=Match.MatchStatus.SCHEDULED,
            deadline=timezone.now() - timedelta(hours=2),
        )
        self.rating_before_strong = float(self.strong.total_points)
        self.rating_before_weak = float(self.weak.total_points)

    def test_process_overdue_notifies_admin_without_closing_match(self) -> None:
        from apps.users.models import Notification, User

        admin = User.objects.create_user(
            email="rr-overdue-admin@test.local",
            password="testpass123",
            is_staff=True,
        )

        ok, msg = process_overdue_match(self.match)

        self.assertTrue(ok, msg)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.SCHEDULED)
        self.assertIsNone(self.match.winner_id)
        self.assertIsNotNone(self.match.deadline_overdue_notified_at)
        self.assertTrue(
            Notification.objects.filter(
                user=admin, message__contains="Просрочен дедлайн"
            ).exists()
        )

    def test_process_overdue_skips_when_deadline_not_passed(self) -> None:
        self.match.deadline = timezone.now() + timedelta(days=1)
        self.match.save(update_fields=["deadline"])

        ok, msg = process_overdue_match(self.match)

        self.assertFalse(ok)
        self.assertIn("Дедлайн не истёк", msg)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.SCHEDULED)

    def test_process_overdue_does_not_change_rating(self) -> None:
        """Просрочка только уведомляет админа, рейтинг игроков не меняется."""
        from apps.users.models import User

        User.objects.create_user(
            email="rr-overdue-admin2@test.local",
            password="testpass123",
            is_staff=True,
        )

        ok, _ = process_overdue_match(self.match)
        self.assertTrue(ok)

        self.strong.refresh_from_db()
        self.weak.refresh_from_db()
        self.match.refresh_from_db()

        self.assertEqual(self.strong.total_points, self.rating_before_strong)
        self.assertEqual(self.weak.total_points, self.rating_before_weak)
        self.assertEqual(self.strong.matches_won, 0)
        self.assertEqual(self.weak.matches_won, 0)
        self.assertEqual(self.strong.matches_played, 15)
        self.assertEqual(self.weak.matches_played, 15)
        self.assertEqual(
            self.match.rating_status, Match.RatingCalcStatus.NOT_APPLICABLE
        )
