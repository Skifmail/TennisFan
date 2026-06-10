"""Тесты согласованности счёта и победителя в заявке на результат матча."""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from apps.tournaments.models import Match, MatchResultProposal, TournamentStatus
from apps.tournaments.proposal_service import (
    ProposalValidationError,
    apply_proposal,
    derive_proposer_result_from_score,
    validate_proposal_score_consistency,
)
from tests.support.factories import make_player, make_tournament


class DeriveProposerResultTestCase(TestCase):
    """Определение WIN/LOSS по счёту для инициатора заявки."""

    def setUp(self) -> None:
        self.player1 = make_player(email_suffix="derive-p1", points=3000.0)
        self.player2 = make_player(email_suffix="derive-p2", points=2800.0)
        self.tournament = make_tournament(
            name="Круговой derive result",
            slug="rr-derive-result",
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

    def test_loser_proposer_gets_loss_when_opponent_won_sets(self) -> None:
        """Сценарий Вера/Лидия: счёт 6:2 6:1 в пользу player1, вносит player2."""
        result = derive_proposer_result_from_score(
            self.match,
            self.player2,
            player1_set1=6,
            player2_set1=2,
            player1_set2=6,
            player2_set2=1,
            player1_set3=None,
            player2_set3=None,
        )
        self.assertEqual(result, Match.ResultChoice.LOSS)

    def test_inconsistent_proposal_is_rejected_on_apply(self) -> None:
        proposal = MatchResultProposal(
            match=self.match,
            proposer=self.player2,
            result=Match.ResultChoice.WIN,
            player1_set1=6,
            player2_set1=2,
            player1_set2=6,
            player2_set2=1,
        )
        with self.assertRaises(ProposalValidationError):
            validate_proposal_score_consistency(proposal)

    def test_consistent_proposal_sets_winner_from_score(self) -> None:
        proposal = MatchResultProposal.objects.create(
            match=self.match,
            proposer=self.player2,
            result=Match.ResultChoice.LOSS,
            player1_set1=6,
            player2_set1=2,
            player1_set2=6,
            player2_set2=1,
        )
        apply_proposal(proposal)
        self.match.refresh_from_db()
        self.assertEqual(self.match.winner_id, self.player1.pk)
        self.assertEqual(self.match.status, Match.MatchStatus.COMPLETED)
