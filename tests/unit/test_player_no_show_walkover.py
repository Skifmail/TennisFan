"""Неявка, внесённая игроком: Walkover без счёта и штраф только виновной стороне."""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from apps.tournaments.models import Match, MatchResultProposal, TournamentStatus
from apps.tournaments.overdue import apply_no_show_walkover
from apps.tournaments.proposal_service import (
    ProposalValidationError,
    apply_proposal,
    derive_proposer_result_from_score,
    no_show_sides_from_proposal,
)
from apps.tournaments.rating import WALKOVER_NO_SHOW_PENALTY
from tests.support.factories import make_player, make_tournament


class PlayerNoShowWalkoverTestCase(TestCase):
    """Тех. победа и тех. поражение от игрока создают неявку без счёта."""

    def setUp(self) -> None:
        self.p1 = make_player(email_suffix="pwo-p1", points=3000.0)
        self.p2 = make_player(email_suffix="pwo-p2", points=2500.0)
        self.tournament = make_tournament(
            name="Турнир неявка игрока",
            slug="player-no-show",
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
            player1=self.p1,
            player2=self.p2,
            status=Match.MatchStatus.SCHEDULED,
        )

    def _propose(self, proposer, result: str) -> MatchResultProposal:
        """Создать и применить заявку без счёта."""
        proposal = MatchResultProposal.objects.create(
            match=self.match,
            proposer=proposer,
            result=result,
        )
        apply_proposal(proposal)
        return proposal

    def test_opponent_no_show_penalizes_only_opponent(self) -> None:
        self._propose(self.p1, Match.ResultChoice.WALKOVER_WIN)

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.WALKOVER)
        self.assertEqual(self.match.winner_id, self.p1.pk)
        self.assertTrue(self.match.is_no_show_walkover())
        self.assertFalse(self.match.is_walkover_loss())
        self.assertIsNone(self.match.player1_set1)
        self.assertEqual(self.match.get_no_show_sides(), (False, True))
        self.assertEqual(self.p1.total_points, 3000.0)
        self.assertEqual(self.p2.total_points, 2500.0 - WALKOVER_NO_SHOW_PENALTY)

    def test_self_no_show_penalizes_proposer(self) -> None:
        self._propose(self.p2, Match.ResultChoice.WALKOVER_LOSS)

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.match.winner_id, self.p1.pk)
        self.assertEqual(self.match.get_no_show_sides(), (False, True))
        self.assertEqual(self.p1.total_points, 3000.0)
        self.assertEqual(self.p2.total_points, 2500.0 - WALKOVER_NO_SHOW_PENALTY)

    def test_no_show_reported_by_second_side(self) -> None:
        self._propose(self.p2, Match.ResultChoice.WALKOVER_WIN)

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.match.winner_id, self.p2.pk)
        self.assertEqual(self.match.get_no_show_sides(), (True, False))
        self.assertEqual(self.p1.total_points, 3000.0 - WALKOVER_NO_SHOW_PENALTY)
        self.assertEqual(self.p2.total_points, 2500.0)

    def test_sides_are_derived_from_proposal(self) -> None:
        proposal = MatchResultProposal(
            match=self.match,
            proposer=self.p1,
            result=Match.ResultChoice.WALKOVER_LOSS,
        )

        self.assertEqual(no_show_sides_from_proposal(proposal), (True, False))

    def test_admin_can_edit_player_no_show(self) -> None:
        self._propose(self.p1, Match.ResultChoice.WALKOVER_WIN)
        self.match.refresh_from_db()

        apply_no_show_walkover(
            self.match,
            side1_no_show=True,
            side2_no_show=True,
            replace=True,
        )

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertTrue(self.match.is_mutual_no_show_walkover())
        self.assertIsNone(self.match.winner_id)
        self.assertEqual(self.p1.total_points, 3000.0 - WALKOVER_NO_SHOW_PENALTY)
        self.assertEqual(self.p2.total_points, 2500.0 - WALKOVER_NO_SHOW_PENALTY)


class ZeroScoreIsRejectedTestCase(TestCase):
    """Счёт 0:0 не принимается: игроку предлагается отметить неявку."""

    def setUp(self) -> None:
        self.p1 = make_player(email_suffix="zero-p1", points=3000.0)
        self.p2 = make_player(email_suffix="zero-p2", points=2500.0)
        self.tournament = make_tournament(
            name="Турнир нулевой счёт",
            slug="zero-score",
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
            player1=self.p1,
            player2=self.p2,
            status=Match.MatchStatus.SCHEDULED,
        )

    def test_zero_score_hints_at_no_show(self) -> None:
        with self.assertRaises(ProposalValidationError) as ctx:
            derive_proposer_result_from_score(
                self.match,
                self.p1,
                player1_set1=0,
                player2_set1=0,
                player1_set2=0,
                player2_set2=0,
                player1_set3=None,
                player2_set3=None,
            )

        message = str(ctx.exception).lower()
        self.assertIn("0:0", message)
        self.assertIn("не явился", message)

    def test_zero_score_is_rejected_on_apply(self) -> None:
        proposal = MatchResultProposal.objects.create(
            match=self.match,
            proposer=self.p1,
            result=Match.ResultChoice.WIN,
            player1_set1=0,
            player2_set1=0,
        )

        with self.assertRaises(ProposalValidationError):
            apply_proposal(proposal)

        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.SCHEDULED)
