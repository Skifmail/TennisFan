"""Подписи ленты для парного и командного спарринга."""

from django.test import SimpleTestCase

from apps.core.activity import doubles_sparring_noun
from apps.core.models import PlatformActivityEvent


class DoublesActivityLabelTest(SimpleTestCase):
    """Бейдж и текст события должны называть один и тот же вид спарринга."""

    def test_old_team_event_badge_matches_description(self) -> None:
        event = PlatformActivityEvent(
            event_type=PlatformActivityEvent.EventType.DOUBLES_CREATED,
            description="Создал парный спарринг (Командный спарринг)",
        )
        self.assertEqual(event.get_feed_badge(), "Создан командный спарринг")
        self.assertEqual(event.get_feed_description(), "Создал командный спарринг")

    def test_old_classic_event_drops_redundant_kind(self) -> None:
        event = PlatformActivityEvent(
            event_type=PlatformActivityEvent.EventType.DOUBLES_CREATED,
            description="Создал парный спарринг (Парный 2×2)",
        )
        self.assertEqual(event.get_feed_badge(), "Создан парный спарринг")
        self.assertEqual(event.get_feed_description(), "Создал парный спарринг")

    def test_metadata_kind_sets_team_badge(self) -> None:
        event = PlatformActivityEvent(
            event_type=PlatformActivityEvent.EventType.DOUBLES_CREATED,
            description="Создал командный спарринг",
            metadata={"sparring_kind": "team"},
        )
        self.assertEqual(event.sparring_kind(), "team")
        self.assertEqual(event.get_feed_badge(), "Создан командный спарринг")

    def test_doubles_sparring_noun(self) -> None:
        self.assertEqual(doubles_sparring_noun("team"), "командный спарринг")
        self.assertEqual(doubles_sparring_noun("classic"), "парный спарринг")
