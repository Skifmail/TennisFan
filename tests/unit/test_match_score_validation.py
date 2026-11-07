"""Допустимые завершённые сеты и форматы матчей тенниса и падела."""

from collections.abc import Sequence

from django.test import SimpleTestCase

from apps.core.sports import Sport
from apps.tournaments.models import Match, MatchFormat, MatchResultProposal, Tournament
from apps.tournaments.proposal_service import (
    ProposalValidationError,
    validate_proposal_score_consistency,
)
from apps.users.models import Player


class MatchScoreValidationTests(SimpleTestCase):
    """Проверки общего валидатора, используемого сайтом и Telegram."""

    def _proposal(
        self, scores: Sequence[tuple[int | None, int | None]], match_format: str = ""
    ) -> MatchResultProposal:
        player1, player2 = Player(pk=1), Player(pk=2)
        match = Match(
            player1=player1,
            player2=player2,
            tournament=Tournament(match_format=match_format),
        )
        fields: dict[str, int | None] = {}
        for index, (a, b) in enumerate(scores, 1):
            fields[f"player1_set{index}"] = a
            fields[f"player2_set{index}"] = b
        return MatchResultProposal(
            match=match, proposer=player1, result=Match.ResultChoice.WIN, **fields
        )

    def test_valid_standard_and_extended_deciding_tiebreak_scores(self) -> None:
        for scores in (
            [(6, 0)],
            [(7, 5)],
            [(7, 6)],
            [(6, 4), (6, 3)],
            [(6, 4), (3, 6), (10, 7)],
            [(6, 4), (3, 6), (12, 10)],
            [(6, 4), (3, 6), (20, 18)],
        ):
            for sport in (Sport.TENNIS, Sport.PADEL):
                with self.subTest(scores=scores, sport=sport):
                    proposal = self._proposal(scores)
                    proposal.match.sport = sport
                    validate_proposal_score_consistency(proposal)

    def test_incomplete_impossible_and_extra_sets_are_rejected(self) -> None:
        for scores in (
            [(20, 1), (20, 1)],
            [(6, 5)],
            [(6, 6)],
            [(8, 6)],
            [(-1, 6)],
            [(6, None)],
            [(6, 4), (None, None), (6, 3)],
            [(6, 4), (6, 3), (6, 2)],
            [(6, 4), (3, 6)],
            [(6, 4), (3, 6), (11, 8)],
            [(6, 4), (3, 6), (10, 9)],
            [(10, 7)],
        ):
            with self.subTest(scores=scores):
                with self.assertRaises(ProposalValidationError):
                    validate_proposal_score_consistency(self._proposal(scores))

    def test_explicit_match_formats_control_set_count_and_short_sets(self) -> None:
        for match_format, scores in (
            (MatchFormat.SET_6, [(6, 4)]),
            (MatchFormat.SET_TIEBREAK, [(7, 6)]),
            (MatchFormat.BEST_OF_2, [(6, 4), (6, 3)]),
            (MatchFormat.FAST4, [(4, 2), (4, 3)]),
            (MatchFormat.FAST4, [(4, 2), (2, 4), (10, 8)]),
        ):
            with self.subTest(match_format=match_format, scores=scores):
                validate_proposal_score_consistency(
                    self._proposal(scores, match_format)
                )
        for match_format, scores in (
            (MatchFormat.SET_6, [(6, 4), (6, 3)]),
            (MatchFormat.BEST_OF_2, [(6, 4)]),
            (MatchFormat.FAST4, [(6, 4), (6, 3)]),
        ):
            with self.subTest(match_format=match_format, scores=scores):
                with self.assertRaises(ProposalValidationError):
                    validate_proposal_score_consistency(
                        self._proposal(scores, match_format)
                    )
