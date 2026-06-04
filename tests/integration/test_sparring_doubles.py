"""Интеграционные тесты: парный спарринг 2×2."""

from django.test import TestCase

from apps.sparring.doubles_services import (
    accept_join_request,
    add_partner_to_author_team,
    cancel_join_request,
    cancel_match_request,
    confirm_match,
    create_doubles_request,
    create_join_request,
    reject_join_request,
)
from apps.sparring.models import (
    DoublesJoinRequestStatus,
    DoublesMatchRequestStatus,
    TeamSide,
)
from apps.tournaments.models import Match
from tests.support.factories import make_player


class DoublesRequestFlowTestCase(TestCase):
    """Создание заявки, отклик solo, принятие, добавление партнёра, подтверждение матча."""

    def setUp(self) -> None:
        self.author = make_player(email_suffix="author")
        self.partner_a = make_player(email_suffix="partner_a")
        self.opp1 = make_player(email_suffix="opp1")
        self.opp2 = make_player(email_suffix="opp2")

    def test_create_request_and_join_solo_then_accept(self) -> None:
        req = create_doubles_request(
            created_by=self.author,
            city="Москва",
            is_friendly=False,
            partner=None,
        )
        self.assertEqual(req.status, DoublesMatchRequestStatus.FORMING)

        jr = create_join_request(
            match_request_id=req.pk,
            created_by=self.opp1,
            target_side=TeamSide.OPPONENT[0],
            players=[self.opp1],
        )
        self.assertEqual(jr.status, DoublesJoinRequestStatus.PENDING)

        accept_join_request(
            match_request_id=req.pk,
            join_request_id=jr.pk,
            accepted_by=self.author,
        )
        jr.refresh_from_db()
        self.assertEqual(jr.status, DoublesJoinRequestStatus.ACCEPTED)

        req.refresh_from_db()
        self.assertEqual(req.status, DoublesMatchRequestStatus.FORMING)

    def test_full_flow_confirm_match_creates_match(self) -> None:
        req = create_doubles_request(
            created_by=self.author,
            city="Москва",
            is_friendly=False,
            partner=self.partner_a,
        )
        create_join_request(
            match_request_id=req.pk,
            created_by=self.opp1,
            target_side=TeamSide.OPPONENT[0],
            players=[self.opp1, self.opp2],
        )
        jr = req.join_requests.filter(status=DoublesJoinRequestStatus.PENDING).first()
        accept_join_request(
            match_request_id=req.pk,
            join_request_id=jr.pk,
            accepted_by=self.author,
        )

        req.refresh_from_db()
        self.assertEqual(req.status, DoublesMatchRequestStatus.READY)

        match = confirm_match(match_request_id=req.pk, confirmed_by=self.author)
        self.assertIsNotNone(match)
        self.assertEqual(match.match_type, Match.MatchType.SPARRING)
        self.assertEqual(match.player1_id, self.author.id)
        self.assertEqual(match.partner1_id, self.partner_a.id)
        self.assertEqual(match.player2_id, self.opp1.id)
        self.assertEqual(match.partner2_id, self.opp2.id)
        self.assertIsNone(match.tournament_id)
        self.assertIsNone(match.winner_id)

        req.refresh_from_db()
        self.assertEqual(req.status, DoublesMatchRequestStatus.CONFIRMED)
        self.assertEqual(req.match_id, match.pk)

    def test_reject_join_request(self) -> None:
        req = create_doubles_request(created_by=self.author, partner=None)
        jr = create_join_request(
            match_request_id=req.pk,
            created_by=self.opp1,
            target_side=TeamSide.OPPONENT[0],
            players=[self.opp1],
        )
        reject_join_request(
            match_request_id=req.pk,
            join_request_id=jr.pk,
            rejected_by=self.author,
        )
        jr.refresh_from_db()
        self.assertEqual(jr.status, DoublesJoinRequestStatus.REJECTED)

    def test_cancel_join_request(self) -> None:
        req = create_doubles_request(created_by=self.author, partner=None)
        jr = create_join_request(
            match_request_id=req.pk,
            created_by=self.opp1,
            target_side=TeamSide.OPPONENT[0],
            players=[self.opp1],
        )
        cancel_join_request(join_request_id=jr.pk, cancelled_by=self.opp1)
        jr.refresh_from_db()
        self.assertEqual(jr.status, DoublesJoinRequestStatus.CANCELLED)

    def test_add_partner_to_author_team(self) -> None:
        req = create_doubles_request(created_by=self.author, partner=None)
        add_partner_to_author_team(
            match_request_id=req.pk,
            player_id=self.partner_a.id,
            added_by=self.author,
        )
        author_team = req.teams.get(side=TeamSide.AUTHOR)
        self.assertEqual(author_team.members.count(), 2)

    def test_cancel_match_request(self) -> None:
        req = create_doubles_request(created_by=self.author, partner=None)
        cancel_match_request(match_request_id=req.pk, cancelled_by=self.author)
        req.refresh_from_db()
        self.assertEqual(req.status, DoublesMatchRequestStatus.CANCELLED)


class DoublesMatchRatingTestCase(TestCase):
    """Завершение парного спарринга: начисление очков и статистики; дружеский — без начисления."""

    def setUp(self) -> None:
        self.p1 = make_player(email_suffix="p1", points=1000.0)
        self.p2 = make_player(email_suffix="p2", points=1000.0)
        self.p3 = make_player(email_suffix="p3", points=1000.0)
        self.p4 = make_player(email_suffix="p4", points=1000.0)

    def test_completed_doubles_sparring_updates_stats_and_rating(self) -> None:
        req = create_doubles_request(
            created_by=self.p1,
            is_friendly=False,
            partner=self.p2,
        )
        create_join_request(
            match_request_id=req.pk,
            created_by=self.p3,
            target_side=TeamSide.OPPONENT[0],
            players=[self.p3, self.p4],
        )
        jr = req.join_requests.get(status=DoublesJoinRequestStatus.PENDING)
        accept_join_request(
            match_request_id=req.pk,
            join_request_id=jr.pk,
            accepted_by=self.p1,
        )
        match = confirm_match(match_request_id=req.pk, confirmed_by=self.p1)

        before = {
            self.p1.id: (
                self.p1.matches_played,
                self.p1.matches_won,
                self.p1.total_points,
            ),
            self.p2.id: (
                self.p2.matches_played,
                self.p2.matches_won,
                self.p2.total_points,
            ),
            self.p3.id: (
                self.p3.matches_played,
                self.p3.matches_won,
                self.p3.total_points,
            ),
            self.p4.id: (
                self.p4.matches_played,
                self.p4.matches_won,
                self.p4.total_points,
            ),
        }

        match.winner_id = self.p1.id
        match.status = Match.MatchStatus.COMPLETED
        match.player1_set1 = 6
        match.player2_set1 = 4
        match.player1_set2 = 6
        match.player2_set2 = 3
        match.rating_status = Match.RatingCalcStatus.PENDING
        match.save()

        for p in (self.p1, self.p2, self.p3, self.p4):
            p.refresh_from_db()

        self.assertEqual(self.p1.matches_played, before[self.p1.id][0] + 1)
        self.assertEqual(self.p1.matches_won, before[self.p1.id][1] + 1)
        self.assertEqual(self.p2.matches_played, before[self.p2.id][0] + 1)
        self.assertEqual(self.p2.matches_won, before[self.p2.id][1] + 1)
        self.assertEqual(self.p3.matches_played, before[self.p3.id][0] + 1)
        self.assertEqual(self.p3.matches_won, before[self.p3.id][1])
        self.assertEqual(self.p4.matches_played, before[self.p4.id][0] + 1)
        self.assertEqual(self.p4.matches_won, before[self.p4.id][1])

        self.assertGreater(self.p1.total_points, before[self.p1.id][2])
        self.assertGreater(self.p2.total_points, before[self.p2.id][2])
        self.assertLess(self.p3.total_points, before[self.p3.id][2])
        self.assertLess(self.p4.total_points, before[self.p4.id][2])

    def test_friendly_doubles_sparring_no_rating_change(self) -> None:
        req = create_doubles_request(
            created_by=self.p1,
            is_friendly=True,
            partner=self.p2,
        )
        create_join_request(
            match_request_id=req.pk,
            created_by=self.p3,
            target_side=TeamSide.OPPONENT[0],
            players=[self.p3, self.p4],
        )
        jr = req.join_requests.get(status=DoublesJoinRequestStatus.PENDING)
        accept_join_request(
            match_request_id=req.pk,
            join_request_id=jr.pk,
            accepted_by=self.p1,
        )
        match = confirm_match(match_request_id=req.pk, confirmed_by=self.p1)

        before_played = {
            self.p1.id: self.p1.matches_played,
            self.p2.id: self.p2.matches_played,
            self.p3.id: self.p3.matches_played,
            self.p4.id: self.p4.matches_played,
        }
        before_points = {
            self.p1.id: self.p1.total_points,
            self.p2.id: self.p2.total_points,
            self.p3.id: self.p3.total_points,
            self.p4.id: self.p4.total_points,
        }

        match.winner_id = self.p1.id
        match.status = Match.MatchStatus.COMPLETED
        match.player1_set1 = 6
        match.player2_set1 = 2
        match.player1_set2 = 6
        match.player2_set2 = 1
        match.rating_status = Match.RatingCalcStatus.NOT_APPLICABLE
        match.save()

        for p in (self.p1, self.p2, self.p3, self.p4):
            p.refresh_from_db()

        for pid, p in [
            (self.p1.id, self.p1),
            (self.p2.id, self.p2),
            (self.p3.id, self.p3),
            (self.p4.id, self.p4),
        ]:
            self.assertEqual(
                p.matches_played,
                before_played[pid],
                f"Player {pid} matches_played must not change for friendly",
            )
            self.assertEqual(
                p.total_points,
                before_points[pid],
                f"Player {pid} total_points must not change for friendly",
            )
