"""Юнит-тесты: формы клуба."""

from datetime import date

from django.test import TestCase

from apps.clubs.forms import ClubPlayerPlanForm, ClubTournamentCreateForm
from apps.clubs.models import (
    Club,
)
from apps.core.geo import GeoRegion
from apps.core.models import GeoArea
from apps.tournaments.models import (
    TournamentFormat,
    TournamentGender,
    TournamentType,
)


class ClubTournamentCreateFormTestCase(TestCase):
    def setUp(self) -> None:
        self.club = Club.objects.create(
            name="Тестовый клуб",
            slug="test-club",
            city="Москва",
            address="ул. Пушкина, 1",
            email="club@test.local",
            admin_name="Администратор клуба",
        )

    def test_slug_is_generated_automatically_when_left_blank(self) -> None:
        form = ClubTournamentCreateForm(
            data={
                "name": "Клубный кубок",
                "slug": "",
                "format": TournamentFormat.WEEKEND_DAY,
                "variant": "singles",
                "entry_fee": "1000",
                "is_one_day": "",
                "city": "Москва",
                "gender": TournamentGender.MALE,
                "allowed_categories": ["amateur"],
                "tournament_type": TournamentType.REGULAR,
                "start_date": date.today().isoformat(),
                "match_days_per_round": 7,
                "fan_points_r1": 10,
                "fan_points_r2": 25,
                "fan_points_sf": 45,
                "fan_points_final": 70,
                "fan_points_winner": 100,
            },
            club=self.club,
            is_pro=False,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["slug"], "test-club-tournament")

    def _base_tournament_data(self, **overrides: str) -> dict[str, str | list[str]]:
        data: dict[str, str | list[str]] = {
            "name": "Клубный кубок",
            "slug": "",
            "format": TournamentFormat.WEEKEND_DAY,
            "variant": "singles",
            "entry_fee": "1000",
            "is_one_day": "",
            "city": "Москва",
            "gender": TournamentGender.MALE,
            "allowed_categories": ["amateur"],
            "tournament_type": TournamentType.REGULAR,
            "start_date": date.today().isoformat(),
            "match_days_per_round": "7",
            "fan_points_r1": "10",
            "fan_points_r2": "25",
            "fan_points_sf": "45",
            "fan_points_final": "70",
            "fan_points_winner": "100",
        }
        data.update(overrides)
        return data

    def test_moscow_club_defaults_region(self) -> None:
        form = ClubTournamentCreateForm(club=self.club, is_pro=False)
        self.assertEqual(form.fields["region"].initial, GeoRegion.MOSCOW)

    def test_geo_area_must_match_region(self) -> None:
        oblast_area = GeoArea.objects.filter(region=GeoRegion.MOSCOW_OBLAST).first()
        self.assertIsNotNone(oblast_area)
        form = ClubTournamentCreateForm(
            data=self._base_tournament_data(
                region=GeoRegion.MOSCOW,
                geo_area=str(oblast_area.pk),
            ),
            club=self.club,
            is_pro=False,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("geo_area", form.errors)

    def test_geo_area_sets_region_when_blank(self) -> None:
        area = GeoArea.objects.filter(region=GeoRegion.MOSCOW).first()
        self.assertIsNotNone(area)
        form = ClubTournamentCreateForm(
            data=self._base_tournament_data(region="", geo_area=str(area.pk)),
            club=self.club,
            is_pro=False,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["region"], GeoRegion.MOSCOW)
        self.assertEqual(form.cleaned_data["geo_area"], area)

    def test_geo_area_queryset_filters_by_region(self) -> None:
        moscow = GeoArea.objects.filter(region=GeoRegion.MOSCOW).first()
        oblast = GeoArea.objects.filter(region=GeoRegion.MOSCOW_OBLAST).first()
        self.assertIsNotNone(moscow)
        self.assertIsNotNone(oblast)
        form = ClubTournamentCreateForm(
            data={"region": GeoRegion.MOSCOW},
            club=self.club,
            is_pro=False,
        )
        ids = set(form.fields["geo_area"].queryset.values_list("pk", flat=True))
        self.assertIn(moscow.pk, ids)
        self.assertNotIn(oblast.pk, ids)


class ClubPlayerPlanFormTestCase(TestCase):
    def test_unlimited_registrations_clear_limit(self) -> None:
        form = ClubPlayerPlanForm(
            data={
                "name": "Безлимит",
                "description": "",
                "is_active": "on",
                "monthly_fee": "1500",
                "duration_days": "45",
                "has_unlimited_registrations": "on",
                "registration_limit_period": "monthly",
                "max_tournaments_per_month": "9",
                "allow_self_change": "on",
                "sort_order": "0",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["max_tournaments_per_month"])

    def test_limited_plan_requires_monthly_limit(self) -> None:
        form = ClubPlayerPlanForm(
            data={
                "name": "Лимитный",
                "description": "",
                "is_active": "on",
                "monthly_fee": "900",
                "duration_days": "30",
                "allow_self_change": "on",
                "sort_order": "0",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("max_tournaments_per_month", form.errors)
