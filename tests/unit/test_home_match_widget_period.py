"""Тесты фильтра периода виджета матчей на главной."""

from __future__ import annotations

from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.tournaments.models import Match, Tournament, TournamentStatus
from apps.users.models import Player, User


class HomeMatchWidgetPeriodTestCase(TestCase):
    """API виджета матчей фильтрует записи по периоду."""

    def setUp(self) -> None:
        self.client = Client()
        self.player1 = Player.objects.create(
            user=User.objects.create_user(
                email="widget-period-p1@test.local",
                password="testpass123",
                first_name="Игрок",
                last_name="Один",
            )
        )
        self.player2 = Player.objects.create(
            user=User.objects.create_user(
                email="widget-period-p2@test.local",
                password="testpass123",
                first_name="Игрок",
                last_name="Два",
            )
        )
        self.tournament = Tournament.objects.create(
            name="Виджет период",
            slug="widget-period",
            city="Казань",
            start_date=timezone.now().date(),
            format="round_robin",
            status=TournamentStatus.ACTIVE,
        )

    def test_recent_matches_today_excludes_older_completed(self) -> None:
        """Сыгранные: период today не включает матч старше суток."""
        Match.objects.create(
            tournament=self.tournament,
            player1=self.player1,
            player2=self.player2,
            status=Match.MatchStatus.COMPLETED,
            completed_datetime=timezone.now() - timedelta(days=3),
            winner=self.player1,
            player1_set1=6,
            player2_set1=4,
        )
        recent = Match.objects.create(
            tournament=self.tournament,
            player1=self.player1,
            player2=self.player2,
            status=Match.MatchStatus.COMPLETED,
            completed_datetime=timezone.now() - timedelta(hours=2),
            winner=self.player1,
            player1_set1=6,
            player2_set1=3,
        )

        response = self.client.get(
            reverse("api_recent_matches"),
            {"period": "today"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["period"], "today")
        self.assertEqual(len(payload["matches"]), 1)
        self.assertEqual(payload["matches"][0]["id"], recent.pk)

    def test_winner_side_follows_score_when_winner_field_wrong(self) -> None:
        """Победитель в виджете определяется по счёту, даже если winner в БД неверный."""
        Match.objects.create(
            tournament=self.tournament,
            player1=self.player1,
            player2=self.player2,
            status=Match.MatchStatus.COMPLETED,
            completed_datetime=timezone.now() - timedelta(hours=1),
            winner=self.player2,
            player1_set1=6,
            player2_set1=2,
            player1_set2=6,
            player2_set2=1,
            rating_delta_player1=15.0,
            rating_delta_player2=-15.0,
        )

        response = self.client.get(reverse("api_recent_matches"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["matches"][0]["winner_side"], "player1")

    def test_recent_matches_api_includes_winner_side(self) -> None:
        """API сыгранных матчей возвращает сторону победителя для виджета."""
        Match.objects.create(
            tournament=self.tournament,
            player1=self.player1,
            player2=self.player2,
            status=Match.MatchStatus.COMPLETED,
            completed_datetime=timezone.now() - timedelta(hours=1),
            winner=self.player1,
            player1_set1=6,
            player2_set1=2,
            player1_set2=6,
            player2_set2=1,
            rating_delta_player1=15.0,
            rating_delta_player2=-15.0,
        )

        response = self.client.get(reverse("api_recent_matches"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["matches"][0]["winner_side"], "player1")

    def test_upcoming_matches_week_excludes_far_future(self) -> None:
        """Предстоящие: период week не включает матч дальше 7 дней."""
        near = Match.objects.create(
            tournament=self.tournament,
            player1=self.player1,
            player2=self.player2,
            status=Match.MatchStatus.SCHEDULED,
            deadline=timezone.now() + timedelta(days=2),
        )
        Match.objects.create(
            tournament=self.tournament,
            player1=self.player1,
            player2=self.player2,
            status=Match.MatchStatus.SCHEDULED,
            deadline=timezone.now() + timedelta(days=20),
        )

        response = self.client.get(
            reverse("api_upcoming_matches"),
            {"period": "week"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["period"], "week")
        self.assertEqual(len(payload["matches"]), 1)
        self.assertEqual(payload["matches"][0]["id"], near.pk)
