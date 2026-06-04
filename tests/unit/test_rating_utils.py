"""Юнит-тесты: маппинг NTRP ↔ FAN-очки и категории силы."""

from decimal import Decimal

from django.test import SimpleTestCase

from apps.users.models import SkillLevel
from apps.users.rating_utils import (
    get_starting_points,
    map_ntrp_to_skill_level,
    rating_to_ntrp_level,
    rating_to_skill_level,
)


class GetStartingPointsTestCase(SimpleTestCase):
    """Линейное отображение силы в стартовые FAN-очки."""

    def test_level_maps_to_thousand_times_rating(self) -> None:
        self.assertEqual(get_starting_points(Decimal("3.5")), 3500)

    def test_boundaries(self) -> None:
        self.assertEqual(get_starting_points(Decimal("1.5")), 1500)
        self.assertEqual(get_starting_points(Decimal("7.0")), 7000)

    def test_out_of_range_raises(self) -> None:
        with self.assertRaises(ValueError):
            get_starting_points(Decimal("1.4"))
        with self.assertRaises(ValueError):
            get_starting_points(Decimal("7.1"))


class MapNtrpToSkillLevelTestCase(SimpleTestCase):
    """Границы категорий силы по NTRP."""

    def test_novice_band(self) -> None:
        self.assertEqual(map_ntrp_to_skill_level(Decimal("1.5")), SkillLevel.NOVICE)
        self.assertEqual(map_ntrp_to_skill_level(Decimal("2.4")), SkillLevel.NOVICE)

    def test_amateur_band(self) -> None:
        self.assertEqual(map_ntrp_to_skill_level(Decimal("2.5")), SkillLevel.AMATEUR)
        self.assertEqual(map_ntrp_to_skill_level(Decimal("3.4")), SkillLevel.AMATEUR)

    def test_experienced_band(self) -> None:
        self.assertEqual(
            map_ntrp_to_skill_level(Decimal("3.5")), SkillLevel.EXPERIENCED
        )

    def test_advanced_band(self) -> None:
        self.assertEqual(map_ntrp_to_skill_level(Decimal("4.5")), SkillLevel.ADVANCED)

    def test_professional_band(self) -> None:
        self.assertEqual(
            map_ntrp_to_skill_level(Decimal("5.5")), SkillLevel.PROFESSIONAL
        )
        self.assertEqual(
            map_ntrp_to_skill_level(Decimal("7.0")), SkillLevel.PROFESSIONAL
        )

    def test_below_minimum_clamped_to_novice(self) -> None:
        self.assertEqual(map_ntrp_to_skill_level(Decimal("0.5")), SkillLevel.NOVICE)


class RatingToNtrpAndSkillTestCase(SimpleTestCase):
    """Обратное преобразование очков в NTRP и категорию."""

    def test_rating_to_ntrp_linear(self) -> None:
        self.assertEqual(rating_to_ntrp_level(4200), Decimal("4.2"))

    def test_rating_clamped_at_bounds(self) -> None:
        self.assertEqual(rating_to_ntrp_level(500), Decimal("1.5"))
        self.assertEqual(rating_to_ntrp_level(99999), Decimal("7.0"))

    def test_rating_to_skill_level_matches_ntrp_mapping(self) -> None:
        self.assertEqual(rating_to_skill_level(2500), SkillLevel.AMATEUR)
        self.assertEqual(rating_to_skill_level(6000), SkillLevel.PROFESSIONAL)
