"""Юнит-тесты: формы клуба."""

from datetime import date, datetime, timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.clubs.forms import ClubPlayerPlanForm, ClubTournamentCreateForm
from apps.clubs.models import (
    Club,
)
from apps.core.geo import GeoRegion
from apps.core.models import GeoArea
from apps.core.sports import Sport, VenueSport
from apps.courts.models import Court
from apps.courts.surfaces import PadelSurface
from apps.tournaments.models import (
    Tournament,
    TournamentFormat,
    TournamentGender,
    TournamentStatus,
    TournamentType,
    TournamentVariant,
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
                "sport": "tennis",
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
            "sport": "tennis",
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

    def test_spb_city_clears_moscow_geo_fields(self) -> None:
        area = GeoArea.objects.filter(region=GeoRegion.MOSCOW).first()
        self.assertIsNotNone(area)
        form = ClubTournamentCreateForm(
            data=self._base_tournament_data(
                city="Санкт-Петербург",
                region=GeoRegion.MOSCOW,
                geo_area=str(area.pk),
            ),
            club=self.club,
            is_pro=False,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["region"], "")
        self.assertIsNone(form.cleaned_data["geo_area"])

    def test_spb_club_hides_moscow_geo_on_create(self) -> None:
        club = Club.objects.create(
            name="Питерский клуб",
            slug="spb-club-geo",
            city="Санкт-Петербург",
            address="Невский, 1",
            email="spb-club@test.local",
            admin_name="Администратор клуба",
        )
        form = ClubTournamentCreateForm(club=club, is_pro=False)
        self.assertFalse(form.shows_moscow_geo())
        self.assertFalse(form.fields["region"].initial)

    def test_oblast_club_defaults_geo_area(self) -> None:
        club = Club.objects.create(
            name="Раменский клуб",
            slug="ramenskoe-club-geo",
            city="Раменское",
            address="ул. 1",
            email="ramenskoe-club@test.local",
            admin_name="Администратор клуба",
        )
        area = GeoArea.objects.get(slug="ramenskoe")
        form = ClubTournamentCreateForm(club=club, is_pro=False)
        self.assertTrue(form.shows_moscow_geo())
        self.assertEqual(form.fields["region"].initial, GeoRegion.MOSCOW_OBLAST)
        self.assertEqual(form.fields["geo_area"].initial, area.pk)

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

    @override_settings(LANGUAGE_CODE="ru")
    def test_date_widgets_render_iso_values(self) -> None:
        """При ru-локали type=date/datetime-local показывают ISO-значения."""
        deadline = timezone.make_aware(datetime(2026, 10, 5, 18, 30))
        tournament = Tournament.objects.create(
            name="Даты ISO",
            slug="dates-iso",
            city="Москва",
            club=self.club,
            start_date=date(2026, 10, 10),
            end_date=date(2026, 10, 12),
            registration_deadline=deadline,
            format=TournamentFormat.SINGLE_ELIMINATION,
            status=TournamentStatus.UPCOMING,
            entry_fee=500,
        )
        form = ClubTournamentCreateForm(
            instance=tournament, club=self.club, is_pro=False
        )
        start_html = str(form["start_date"])
        end_html = str(form["end_date"])
        deadline_html = str(form["registration_deadline"])
        self.assertIn('value="2026-10-10"', start_html)
        self.assertIn('value="2026-10-12"', end_html)
        self.assertIn('value="2026-10-05T18:30"', deadline_html)

    @override_settings(LANGUAGE_CODE="ru")
    def test_datetime_local_post_saves_registration_deadline(self) -> None:
        """POST с datetime-local сохраняет дедлайн при LANGUAGE_CODE=ru."""
        start = (date.today() + timedelta(days=14)).isoformat()
        deadline_local = (
            timezone.localtime(timezone.now()) + timedelta(days=10)
        ).strftime("%Y-%m-%dT%H:%M")
        form = ClubTournamentCreateForm(
            data=self._base_tournament_data(
                format=TournamentFormat.SINGLE_ELIMINATION,
                start_date=start,
                registration_deadline=deadline_local,
                postpayment_deadline_hours="12",
            ),
            club=self.club,
            is_pro=False,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNotNone(form.cleaned_data["registration_deadline"])

    def test_sport_field_defaults_to_tennis(self) -> None:
        form = ClubTournamentCreateForm(club=self.club, is_pro=False)
        self.assertIn("sport", form.fields)
        self.assertEqual(form.fields["sport"].initial, Sport.TENNIS)

    def test_padel_forces_doubles_even_if_singles_posted(self) -> None:
        form = ClubTournamentCreateForm(
            data=self._base_tournament_data(
                sport=Sport.PADEL,
                variant="singles",
                max_teams="8",
            ),
            club=self.club,
            is_pro=False,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["sport"], Sport.PADEL)
        self.assertEqual(form.cleaned_data["variant"], TournamentVariant.DOUBLES)
        tournament = form.save(commit=False)
        tournament.club = self.club
        tournament.save()
        tournament.refresh_from_db()
        self.assertEqual(tournament.sport, Sport.PADEL)
        self.assertEqual(tournament.variant, TournamentVariant.DOUBLES)

    def test_padel_rejects_tennis_only_court(self) -> None:
        tennis_court = Court.objects.create(
            name="Теннисный корт клуба",
            slug="club-tennis-court",
            city="Москва",
            address="ул. Кортовая, 1",
            surface="хард",
            venue_sport=VenueSport.TENNIS,
            is_active=True,
        )
        form = ClubTournamentCreateForm(
            data=self._base_tournament_data(
                sport=Sport.PADEL,
                variant="doubles",
                max_teams="8",
                court=str(tennis_court.pk),
            ),
            club=self.club,
            is_pro=False,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("court", form.errors)

    def test_padel_accepts_padel_court(self) -> None:
        padel_court = Court.objects.create(
            name="Падел-корт клуба",
            slug="club-padel-court",
            city="Москва",
            address="ул. Кортовая, 2",
            surface="падел",
            venue_sport=VenueSport.PADEL,
            padel_surfaces=[PadelSurface.ARTIFICIAL_GRASS],
            is_active=True,
        )
        form = ClubTournamentCreateForm(
            data=self._base_tournament_data(
                sport=Sport.PADEL,
                variant="doubles",
                max_teams="8",
                court=str(padel_court.pk),
            ),
            club=self.club,
            is_pro=False,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["court"], padel_court)


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
