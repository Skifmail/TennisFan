"""HTTP: проставить Walkover и сбросить флаг уведомления при продлении дедлайна."""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.tournaments.models import Match, TournamentStatus
from apps.tournaments.rating import WALKOVER_NO_SHOW_PENALTY
from tests.support.factories import make_player, make_tournament, make_user


class TournamentManageWalkoverTestCase(TestCase):
    """Управление турниром: Walkover за неявку и продление дедлайна."""

    def setUp(self) -> None:
        self.admin = make_user(
            email="wo-manage-admin@test.local",
            is_staff=True,
        )
        self.client.force_login(self.admin)
        self.p1 = make_player(email_suffix="wo-manage-p1", points=2800.0)
        self.p2 = make_player(email_suffix="wo-manage-p2", points=2600.0)
        self.tournament = make_tournament(
            name="Управление walkover",
            slug="wo-manage",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            bracket_generated=True,
            start_date=date.today(),
        )
        self.match = Match.objects.create(
            tournament=self.tournament,
            round_name="Тур 1",
            player1=self.p1,
            player2=self.p2,
            status=Match.MatchStatus.SCHEDULED,
            deadline=timezone.now() - timedelta(days=1),
            deadline_overdue_notified_at=timezone.now(),
        )

    def test_post_walkover_assigns_penalty_to_selected_player(self) -> None:
        url = reverse(
            "tournament_manage_match_walkover",
            kwargs={"slug": self.tournament.slug, "pk": self.match.pk},
        )

        response = self.client.post(
            url,
            {"walkover_loser_id": str(self.p2.pk)},
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.WALKOVER)
        self.assertEqual(self.match.winner_id, self.p1.pk)
        self.assertEqual(self.p1.total_points, 2800.0)
        self.assertEqual(self.p2.total_points, 2600.0 - WALKOVER_NO_SHOW_PENALTY)

    def test_post_walkover_uses_custom_penalty_amount(self) -> None:
        url = reverse(
            "tournament_manage_match_walkover",
            kwargs={"slug": self.tournament.slug, "pk": self.match.pk},
        )

        response = self.client.post(
            url,
            {"walkover_loser_id": str(self.p2.pk), "walkover_penalty": "25"},
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.match.refresh_from_db()
        self.assertEqual(self.p1.total_points, 2800.0)
        self.assertEqual(self.p2.total_points, 2575.0)
        self.assertEqual(self.match.rating_delta_player2, -25.0)

    def test_post_walkover_skip_penalty_leaves_rating_unchanged(self) -> None:
        url = reverse(
            "tournament_manage_match_walkover",
            kwargs={"slug": self.tournament.slug, "pk": self.match.pk},
        )

        response = self.client.post(
            url,
            {
                "walkover_loser_id": str(self.p2.pk),
                "walkover_penalty": "40",
                "skip_penalty": "1",
            },
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.WALKOVER)
        self.assertEqual(self.p1.total_points, 2800.0)
        self.assertEqual(self.p2.total_points, 2600.0)
        self.assertEqual(self.match.rating_delta_player1, 0.0)
        self.assertEqual(self.match.rating_delta_player2, 0.0)

    def test_extending_deadline_clears_overdue_notification_flag(self) -> None:
        url = reverse(
            "tournament_manage_match_deadline",
            kwargs={"slug": self.tournament.slug, "pk": self.match.pk},
        )
        new_date = (date.today() + timedelta(days=10)).isoformat()

        response = self.client.post(
            url, {"deadline": new_date}, secure=True, follow=True
        )

        self.assertEqual(response.status_code, 200)
        self.match.refresh_from_db()
        self.assertIsNone(self.match.deadline_overdue_notified_at)
        self.assertEqual(self.match.status, Match.MatchStatus.SCHEDULED)

    def test_manage_page_walkover_button_disabled_until_player_chosen(self) -> None:
        url = reverse("tournament_manage", kwargs={"slug": self.tournament.slug})

        response = self.client.get(url, secure=True)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("data-walkover-form", html)
        self.assertIn("bracket-walkover-btn", html)
        self.assertIn("disabled", html)
        self.assertIn("data-player-name", html)
        self.assertIn("Проставить Walkover (неявку)", html)
        self.assertIn("walkover-confirm-modal", html)
        self.assertIn("Не начислять штраф", html)
        self.assertIn("walkover-penalty-input", html)
