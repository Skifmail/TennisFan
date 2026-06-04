"""Юнит-тесты: валидация метрик и окно редактирования оценки."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.player_ratings.enums import SkillMetric
from apps.player_ratings.models import PlayerSkillRating
from apps.player_ratings.services import can_edit_rating, validate_metric_values
from tests.support.factories import make_player
from tests.support.matches import make_completed_sparring_match


class ValidateMetricValuesTestCase(TestCase):
    """Фильтрация payload оценки соперника."""

    def test_valid_metrics_parsed(self) -> None:
        payload = {"serve": 8, "accuracy": "7", "unknown": 99}
        result = validate_metric_values(payload)

        self.assertEqual(result["serve"], 8)
        self.assertEqual(result["accuracy"], 7)
        self.assertNotIn("unknown", result)
        for name in SkillMetric.all_metric_names():
            self.assertIn(name, result)

    def test_out_of_range_becomes_none(self) -> None:
        result = validate_metric_values({"serve": 0, "tactics": 11})

        self.assertIsNone(result["serve"])
        self.assertIsNone(result["tactics"])

    def test_empty_and_invalid_types_become_none(self) -> None:
        result = validate_metric_values({"speed": "", "endurance": "bad"})

        self.assertIsNone(result["speed"])
        self.assertIsNone(result["endurance"])


class CanEditRatingTestCase(TestCase):
    """Окно редактирования оценки после матча."""

    def setUp(self) -> None:
        self.rater = make_player(email_suffix="rater_edit")
        self.opponent = make_player(email_suffix="opp_edit")
        match = make_completed_sparring_match(self.rater, self.opponent)
        self.rating = PlayerSkillRating.objects.create(
            match=match,
            from_player=self.rater,
            to_player=self.opponent,
            serve=5,
        )

    def test_recent_rating_is_editable(self) -> None:
        self.assertTrue(can_edit_rating(self.rating))

    def test_expired_window_blocks_edit(self) -> None:
        past = timezone.now() - timedelta(hours=25)
        PlayerSkillRating.objects.filter(pk=self.rating.pk).update(
            created_at=past,
            updated_at=past,
        )
        self.rating.refresh_from_db()

        self.assertFalse(can_edit_rating(self.rating))
