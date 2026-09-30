"""Изоляция падела: рейтинг, парный формат турнира и фильтр кортов."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.sports import Sport, VenueSport, parse_sport_filter
from apps.courts.models import Court
from apps.courts.surfaces import PadelSurface, filter_courts_by_venue
from apps.tournaments.models import Tournament, TournamentVariant
from apps.users.models import Player, User
from apps.users.sport_rating import commit_rating, set_padel_strength


class ParseSportFilterTestCase(TestCase):
    """Пустой фильтр каталога остаётся теннисом."""

    def test_default_is_tennis(self) -> None:
        self.assertEqual(parse_sport_filter(None), Sport.TENNIS)
        self.assertEqual(parse_sport_filter("all"), "all")
        self.assertEqual(parse_sport_filter("padel"), Sport.PADEL)


class PadelRatingIsolationTestCase(TestCase):
    """Падел не пишет FAN в поля тенниса на игроке."""

    def setUp(self) -> None:
        self.player = Player.objects.create(
            user=User.objects.create_user(
                email="padel-rating@test.local",
                password="testpass123",
            ),
            total_points=3200,
            hidden_rating=3200,
            ntrp_level=Decimal("3.2"),
        )

    def test_commit_padel_keeps_tennis_points(self) -> None:
        commit_rating(self.player, 4100, Sport.PADEL)
        self.player.refresh_from_db()
        self.assertEqual(self.player.total_points, 3200)
        profile = self.player.sport_profiles.get(sport=Sport.PADEL)
        self.assertEqual(profile.total_points, 4100)

    def test_set_padel_strength_does_not_touch_tennis(self) -> None:
        set_padel_strength(self.player, Decimal("4.0"))
        self.player.refresh_from_db()
        self.assertEqual(self.player.total_points, 3200)
        profile = self.player.sport_profiles.get(sport=Sport.PADEL)
        self.assertEqual(profile.ntrp_level, Decimal("4.0"))
        self.assertEqual(profile.total_points, 4000)


class PadelTournamentDoublesTestCase(TestCase):
    """Падел-турнир сохраняется только как парный."""

    def test_singles_variant_is_forced_to_doubles(self) -> None:
        tournament = Tournament.objects.create(
            name="Падел Казань",
            slug="padel-kazan",
            city="Казань",
            start_date=timezone.now().date(),
            sport=Sport.PADEL,
            variant=TournamentVariant.SINGLES,
        )
        tournament.refresh_from_db()
        self.assertEqual(tournament.variant, TournamentVariant.DOUBLES)


class PadelCourtFilterTestCase(TestCase):
    """Каталог падела видит падел и универсальные корты."""

    def test_venue_filter(self) -> None:
        tennis = Court.objects.create(
            name="Хард",
            slug="hard-court",
            city="Казань",
            address="ул. 1",
            surface="хард",
            venue_sport=VenueSport.TENNIS,
            is_active=True,
        )
        padel = Court.objects.create(
            name="Падел",
            slug="padel-court",
            city="Казань",
            address="ул. 2",
            surface="падел",
            venue_sport=VenueSport.PADEL,
            padel_surfaces=[PadelSurface.ARTIFICIAL_GRASS],
            is_active=True,
        )
        both = Court.objects.create(
            name="Оба",
            slug="both-court",
            city="Казань",
            address="ул. 3",
            surface="хард",
            venue_sport=VenueSport.BOTH,
            is_active=True,
        )
        padel_ids = set(
            filter_courts_by_venue(Court.objects.all(), Sport.PADEL).values_list(
                "pk", flat=True
            )
        )
        self.assertEqual(padel_ids, {padel.pk, both.pk})
        tennis_ids = set(
            filter_courts_by_venue(Court.objects.all(), Sport.TENNIS).values_list(
                "pk", flat=True
            )
        )
        self.assertIn(tennis.pk, tennis_ids)
        self.assertNotIn(padel.pk, tennis_ids)
