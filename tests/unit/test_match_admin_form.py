"""Тесты упрощённой формы матча в Django admin."""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from apps.tournaments.admin import MatchAdminForm
from apps.tournaments.models import Match, TournamentStatus, TournamentTeam
from tests.support.factories import make_player, make_tournament


class MatchAdminFormTestCase(TestCase):
    """Валидация и автозавершение матча через админку."""

    def setUp(self) -> None:
        self.player1 = make_player(email_suffix="admin-form-p1", points=3000.0)
        self.player2 = make_player(email_suffix="admin-form-p2", points=2800.0)
        self.tournament = make_tournament(
            name="Круговой admin form",
            slug="rr-admin-form",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            bracket_generated=True,
            start_date=date.today(),
        )
        self.match = Match.objects.create(
            tournament=self.tournament,
            round_name="Тур 2",
            round_index=2,
            round_order=1,
            player1=self.player1,
            player2=self.player2,
            status=Match.MatchStatus.SCHEDULED,
        )

    def test_score_without_winner_auto_completes_match(self) -> None:
        form = MatchAdminForm(
            data={
                "tournament": self.tournament.pk,
                "round_name": "Тур 2",
                "deadline": "",
                "player1": self.player1.pk,
                "player2": self.player2.pk,
                "team1": "",
                "team2": "",
                "winner_side": "",
                "player1_set1": 3,
                "player2_set1": 6,
                "player1_set2": 0,
                "player2_set2": 6,
                "player1_set3": "",
                "player2_set3": "",
                "status": Match.MatchStatus.SCHEDULED,
                "completed_datetime": "",
                "court": "",
                "round_index": 2,
                "round_order": 1,
                "is_consolation": False,
                "tvd_group": "",
                "tvd_stage": "",
                "next_match": "",
                "loser_next_match": "",
                "placement_min": "",
                "placement_max": "",
                "scheduled_datetime": "",
                "points_player1": 0,
                "points_player2": 0,
                "match_type": Match.MatchType.TOURNAMENT,
                "rating_status": Match.RatingCalcStatus.NOT_APPLICABLE,
                "rating_delta_player1": 0,
                "rating_delta_player2": 0,
            },
            instance=self.match,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()

        saved.refresh_from_db()
        self.assertEqual(saved.status, Match.MatchStatus.COMPLETED)
        self.assertEqual(saved.winner_id, self.player2.pk)
        self.assertIsNotNone(saved.completed_datetime)
        self.assertEqual(saved.rating_status, Match.RatingCalcStatus.CALCULATED)
        self.player1.refresh_from_db()
        self.player2.refresh_from_db()
        self.assertEqual(self.player1.matches_played, 1)
        self.assertEqual(self.player2.matches_played, 1)
        self.assertEqual(self.player2.matches_won, 1)

    def test_winner_without_score_is_rejected(self) -> None:
        form = MatchAdminForm(
            data={
                "tournament": self.tournament.pk,
                "round_name": "Тур 2",
                "deadline": "",
                "player1": self.player1.pk,
                "player2": self.player2.pk,
                "team1": "",
                "team2": "",
                "winner_side": "player2",
                "player1_set1": "",
                "player2_set1": "",
                "player1_set2": "",
                "player2_set2": "",
                "player1_set3": "",
                "player2_set3": "",
                "status": Match.MatchStatus.SCHEDULED,
                "completed_datetime": "",
                "court": "",
                "round_index": 2,
                "round_order": 1,
                "is_consolation": False,
                "tvd_group": "",
                "tvd_stage": "",
                "next_match": "",
                "loser_next_match": "",
                "placement_min": "",
                "placement_max": "",
                "scheduled_datetime": "",
                "points_player1": 0,
                "points_player2": 0,
                "match_type": Match.MatchType.TOURNAMENT,
                "rating_status": Match.RatingCalcStatus.NOT_APPLICABLE,
                "rating_delta_player1": 0,
                "rating_delta_player2": 0,
            },
            instance=self.match,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("счёт", str(form.errors).lower())

    def test_mismatched_score_and_winner_is_rejected(self) -> None:
        form = MatchAdminForm(
            data={
                "tournament": self.tournament.pk,
                "round_name": "Тур 2",
                "deadline": "",
                "player1": self.player1.pk,
                "player2": self.player2.pk,
                "team1": "",
                "team2": "",
                "winner_side": "player1",
                "player1_set1": 3,
                "player2_set1": 6,
                "player1_set2": 0,
                "player2_set2": 6,
                "player1_set3": "",
                "player2_set3": "",
                "status": Match.MatchStatus.SCHEDULED,
                "completed_datetime": "",
                "court": "",
                "round_index": 2,
                "round_order": 1,
                "is_consolation": False,
                "tvd_group": "",
                "tvd_stage": "",
                "next_match": "",
                "loser_next_match": "",
                "placement_min": "",
                "placement_max": "",
                "scheduled_datetime": "",
                "points_player1": 0,
                "points_player2": 0,
                "match_type": Match.MatchType.TOURNAMENT,
                "rating_status": Match.RatingCalcStatus.NOT_APPLICABLE,
                "rating_delta_player1": 0,
                "rating_delta_player2": 0,
            },
            instance=self.match,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("не соответствует", str(form.errors).lower())

    def test_singles_match_accepts_winner_when_players_readonly(self) -> None:
        """Одиночный матч: winner_side валиден, если player1/player2 не в POST (readonly)."""
        form = MatchAdminForm(
            data={
                "tournament": self.tournament.pk,
                "round_name": "Тур 2",
                "deadline": "",
                "player1": "",
                "player2": "",
                "team1": "",
                "team2": "",
                "winner_side": "player2",
                "player1_set1": 4,
                "player2_set1": 6,
                "player1_set2": 1,
                "player2_set2": 6,
                "player1_set3": "",
                "player2_set3": "",
                "status": Match.MatchStatus.SCHEDULED,
                "completed_datetime": "",
                "court": "",
                "round_index": 2,
                "round_order": 1,
                "is_consolation": False,
                "tvd_group": "",
                "tvd_stage": "",
                "next_match": "",
                "loser_next_match": "",
                "placement_min": "",
                "placement_max": "",
                "scheduled_datetime": "",
                "points_player1": 0,
                "points_player2": 0,
                "match_type": Match.MatchType.TOURNAMENT,
                "rating_status": Match.RatingCalcStatus.NOT_APPLICABLE,
                "rating_delta_player1": 0,
                "rating_delta_player2": 0,
            },
            instance=self.match,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        saved.refresh_from_db()
        self.assertEqual(saved.winner_id, self.player2.pk)
        self.assertEqual(saved.status, Match.MatchStatus.COMPLETED)

    def test_doubles_match_accepts_winner_when_teams_readonly(self) -> None:
        """Парный матч: winner_side валиден, даже если team1/team2 не в POST (readonly)."""
        p1 = make_player(email_suffix="dbl-admin-p1", points=3000.0)
        p2 = make_player(email_suffix="dbl-admin-p2", points=2900.0)
        p3 = make_player(email_suffix="dbl-admin-p3", points=2800.0)
        p4 = make_player(email_suffix="dbl-admin-p4", points=2700.0)
        tournament = make_tournament(
            name="Парный admin form",
            slug="doubles-admin-form",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            bracket_generated=True,
            start_date=date.today(),
        )
        team1 = TournamentTeam.objects.create(
            tournament=tournament, player1=p1, player2=p2
        )
        team2 = TournamentTeam.objects.create(
            tournament=tournament, player1=p3, player2=p4
        )
        match = Match.objects.create(
            tournament=tournament,
            round_name="Тур 3",
            round_index=3,
            round_order=1,
            team1=team1,
            team2=team2,
            player1=p1,
            player2=p3,
            status=Match.MatchStatus.SCHEDULED,
        )

        form = MatchAdminForm(
            data={
                "tournament": tournament.pk,
                "round_name": "Тур 3",
                "deadline": "",
                "player1": p1.pk,
                "player2": p3.pk,
                "team1": "",
                "team2": "",
                "winner_side": "team2",
                "player1_set1": 4,
                "player2_set1": 6,
                "player1_set2": 2,
                "player2_set2": 6,
                "player1_set3": "",
                "player2_set3": "",
                "status": Match.MatchStatus.SCHEDULED,
                "completed_datetime": "",
                "court": "",
                "round_index": 3,
                "round_order": 1,
                "is_consolation": False,
                "tvd_group": "",
                "tvd_stage": "",
                "next_match": "",
                "loser_next_match": "",
                "placement_min": "",
                "placement_max": "",
                "scheduled_datetime": "",
                "points_player1": 0,
                "points_player2": 0,
                "match_type": Match.MatchType.TOURNAMENT,
                "rating_status": Match.RatingCalcStatus.NOT_APPLICABLE,
                "rating_delta_player1": 0,
                "rating_delta_player2": 0,
            },
            instance=match,
        )

        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        saved.refresh_from_db()
        self.assertEqual(saved.winner_team_id, team2.pk)
        self.assertEqual(saved.status, Match.MatchStatus.COMPLETED)
