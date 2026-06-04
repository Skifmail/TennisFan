"""Интеграционные тесты: оценка соперников после матча."""

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from apps.player_ratings.constants import MIN_VOTES_TO_DISPLAY
from apps.player_ratings.models import PlayerSkillAggregate, PlayerSkillRating
from apps.player_ratings.services import (
    can_rate_match,
    get_player_skills,
    submit_rating,
)
from apps.tournaments.models import Match
from tests.support.factories import make_player, make_subscription
from tests.support.matches import make_completed_sparring_match


class CanRateMatchTestCase(TestCase):
    """Права на оценку соперника в завершённом матче."""

    def setUp(self) -> None:
        self.rater = make_player(email_suffix="rater_can")
        self.opponent = make_player(email_suffix="opp_can")
        self.match = make_completed_sparring_match(self.rater, self.opponent)

    def test_without_subscription_denied(self) -> None:
        ok, reason = can_rate_match(self.match, self.rater)

        self.assertFalse(ok)
        self.assertIn("подписк", reason.lower())

    def test_with_rating_subscription_allowed(self) -> None:
        make_subscription(
            self.rater.user,
            tier_name="rate-tier",
            tier_kwargs={"can_rate_opponents": True},
        )

        ok, reason = can_rate_match(self.match, self.rater)

        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_non_participant_denied(self) -> None:
        outsider = make_player(email_suffix="outsider_can")
        make_subscription(
            outsider.user,
            tier_name="rate-tier-2",
            tier_kwargs={"can_rate_opponents": True},
        )

        ok, reason = can_rate_match(self.match, outsider)

        self.assertFalse(ok)
        self.assertIn("не участвовали", reason.lower())

    def test_already_rated_denied(self) -> None:
        make_subscription(
            self.rater.user,
            tier_name="rate-tier-3",
            tier_kwargs={"can_rate_opponents": True},
        )
        PlayerSkillRating.objects.create(
            match=self.match,
            from_player=self.rater,
            to_player=self.opponent,
        )

        ok, reason = can_rate_match(self.match, self.rater)

        self.assertFalse(ok)
        self.assertIn("уже оценили", reason.lower())

    def test_walkover_match_allowed(self) -> None:
        make_subscription(
            self.rater.user,
            tier_name="rate-tier-4",
            tier_kwargs={"can_rate_opponents": True},
        )
        self.match.status = Match.MatchStatus.WALKOVER
        self.match.save(update_fields=["status"])

        ok, _ = can_rate_match(self.match, self.rater)

        self.assertTrue(ok)


class SubmitRatingTestCase(TestCase):
    """Сохранение оценки и пересчёт агрегатов."""

    def setUp(self) -> None:
        self.rater = make_player(email_suffix="rater_sub")
        self.opponent = make_player(email_suffix="opp_sub")
        self.match = make_completed_sparring_match(self.rater, self.opponent)
        make_subscription(
            self.rater.user,
            tier_name="rate-tier-submit",
            tier_kwargs={"can_rate_opponents": True},
        )

    def test_submit_creates_rating_and_aggregate(self) -> None:
        ok, message, rating = submit_rating(
            self.match.pk,
            self.rater,
            {"serve": 9, "accuracy": 7},
        )

        self.assertTrue(ok)
        self.assertEqual(message, "Оценка сохранена")
        self.assertIsNotNone(rating)
        assert rating is not None
        self.assertEqual(rating.serve, 9)
        self.assertEqual(rating.to_player_id, self.opponent.pk)

        agg = PlayerSkillAggregate.objects.get(
            player=self.opponent,
            metric_name="serve",
        )
        self.assertEqual(agg.votes_count, 1)
        self.assertEqual(agg.average_raw, 9.0)

    def test_update_within_edit_window(self) -> None:
        submit_rating(self.match.pk, self.rater, {"serve": 5})

        ok, _, rating = submit_rating(self.match.pk, self.rater, {"serve": 8})

        self.assertTrue(ok)
        assert rating is not None
        self.assertEqual(rating.serve, 8)
        agg = PlayerSkillAggregate.objects.get(
            player=self.opponent,
            metric_name="serve",
        )
        self.assertEqual(agg.average_raw, 8.0)
        self.assertEqual(agg.votes_count, 1)


class GetPlayerSkillsTestCase(TestCase):
    """API-агрегаты навыков для профиля."""

    def setUp(self) -> None:
        self.player = make_player(email_suffix="skills_target")
        self.viewer = make_player(email_suffix="skills_viewer")
        rater = make_player(email_suffix="skills_rater")
        match = make_completed_sparring_match(rater, self.player)
        make_subscription(
            rater.user,
            tier_name="rate-tier-skills",
            tier_kwargs={"can_rate_opponents": True},
        )
        submit_rating(match.pk, rater, {"serve": 8})

    def test_insufficient_data_below_vote_threshold(self) -> None:
        data = get_player_skills(self.player, AnonymousUser())

        serve_metric = next(m for m in data["metrics"] if m["name"] == "serve")
        self.assertTrue(serve_metric["insufficient_data"])
        self.assertIsNone(serve_metric["display_value"])
        self.assertLess(serve_metric["votes_count"], MIN_VOTES_TO_DISPLAY)

    def test_owner_lowest_three_only_with_enough_votes(self) -> None:
        data = get_player_skills(
            self.player,
            self.player.user,
            include_lowest_three=True,
        )

        self.assertEqual(data["recommend_to_improve"], [])
