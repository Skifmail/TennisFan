"""Изоляция падела: рейтинг, парный формат турнира и фильтр кортов."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.sports import Sport, VenueSport, parse_sport_filter
from apps.courts.models import Court
from apps.courts.surfaces import PadelSurface, filter_courts_by_venue
from apps.tournaments.models import Tournament, TournamentGender, TournamentVariant
from apps.users.models import Player, SkillLevel, User
from apps.users.sport_rating import (
    PadelStrengthAlreadySet,
    commit_rating,
    ensure_sport_profile,
    padel_entry_complete,
    set_padel_strength,
    skill_level_for,
)


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

    def test_padel_progress_charts_do_not_use_tennis_totals(self) -> None:
        self.player.matches_played = 7
        self.player.matches_won = 5
        self.player.save(update_fields=["matches_played", "matches_won"])
        set_padel_strength(self.player, Decimal("4.0"))
        from apps.users.views import _get_profile_progress_data

        padel_data = _get_profile_progress_data(
            self.player, platform_only=True, sport=Sport.PADEL
        )
        self.assertEqual(padel_data[-1]["matches"], 0)
        self.assertEqual(padel_data[-1]["points"], 4000.0)
        self.assertEqual(padel_data[-1]["win_rate"], 0.0)

        tennis_data = _get_profile_progress_data(
            self.player, platform_only=True, sport=Sport.TENNIS
        )
        self.assertEqual(tennis_data[-1]["matches"], 7)
        self.assertEqual(tennis_data[-1]["points"], 3200.0)
        self.assertEqual(tennis_data[-1]["win_rate"], 71.4)

    def test_set_padel_strength_cannot_be_repeated(self) -> None:
        set_padel_strength(self.player, Decimal("4.0"))
        with self.assertRaises(PadelStrengthAlreadySet):
            set_padel_strength(self.player, Decimal("5.0"))
        profile = self.player.sport_profiles.get(sport=Sport.PADEL)
        self.assertEqual(profile.ntrp_level, Decimal("4.0"))

    def test_ensure_profile_does_not_complete_padel_entry(self) -> None:
        ensure_sport_profile(self.player, Sport.PADEL)
        self.assertFalse(padel_entry_complete(self.player))
        self.assertIsNone(skill_level_for(self.player, Sport.PADEL))


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


class ProfileSportSwitcherTestCase(TestCase):
    """Профиль показывает силу и FAN выбранного вида спорта."""

    def setUp(self) -> None:
        self.player = Player.objects.create(
            user=User.objects.create_user(
                email="profile-sport@test.local",
                password="testpass123",
            ),
            total_points=3200,
            hidden_rating=3200,
            ntrp_level=Decimal("3.2"),
        )
        set_padel_strength(self.player, Decimal("4.0"))
        self.client.force_login(self.player.user)

    def test_default_profile_is_tennis(self) -> None:
        response = self.client.get(
            reverse("profile", kwargs={"pk": self.player.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "?sport=padel")
        self.assertContains(response, "3,20")
        self.assertNotContains(response, "4,00")

    def test_padel_tab_shows_padel_rating(self) -> None:
        response = self.client.get(
            reverse("profile", kwargs={"pk": self.player.pk}),
            {"sport": "padel"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "4,00")
        self.assertNotContains(response, "3,20")


class PadelOnboardingLockTestCase(TestCase):
    """Стартовый уровень падела задаётся один раз и открывает доступ к событиям."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="padel-onboard@test.local",
            password="testpass123",
            first_name="Леонид",
            last_name="Ермолаев",
            phone="+79001234567",
        )
        self.player = Player.objects.create(
            user=self.user,
            total_points=3200,
            hidden_rating=3200,
            ntrp_level=Decimal("3.2"),
            birth_date=date(1981, 2, 5),
            gender="male",
        )
        self.client.force_login(self.user)
        self.tournament = Tournament.objects.create(
            name="Падел онбординг",
            slug="padel-onboarding",
            city="Казань",
            start_date=timezone.now().date(),
            sport=Sport.PADEL,
            gender=TournamentGender.OPEN,
        )
        self.tournament.allowed_categories.create(category=SkillLevel.NOVICE)

    def test_profile_padel_tab_shows_permanent_form(self) -> None:
        response = self.client.get(
            reverse("profile", kwargs={"pk": self.player.pk}),
            {"sport": "padel"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "укажите уровень игры в паделе")
        self.assertContains(response, "Задать уровень")
        self.assertContains(response, "profile-padel-entry")
        self.assertNotContains(response, "Укажите уровень силы в паделе")

    def test_padel_strength_saves_once_and_locks(self) -> None:
        url = reverse("padel_strength")
        first = self.client.post(url, {"level": "3.5"}, secure=True)
        self.assertEqual(first.status_code, 302)
        profile = self.player.sport_profiles.get(sport=Sport.PADEL)
        self.assertEqual(profile.ntrp_level, Decimal("3.5"))
        self.assertTrue(profile.entry_level_set)

        second = self.client.post(url, {"level": "6.0"}, secure=True)
        self.assertEqual(second.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.ntrp_level, Decimal("3.5"))
        self.assertContains(second, "уже зафиксирован")

    def test_tournament_register_redirects_until_level_is_set(self) -> None:
        url = reverse(
            "tournament_register_doubles",
            kwargs={"slug": self.tournament.slug},
        )
        response = self.client.get(url, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("padel_strength"), response["Location"])

        set_padel_strength(self.player, Decimal("3.0"))
        after = self.client.get(url, secure=True)
        self.assertNotIn(reverse("padel_strength"), after.get("Location", ""))

    def test_padel_training_enroll_redirects_until_level_is_set(self) -> None:
        from apps.training.models import Training

        training = Training.objects.create(
            title="Падел группа",
            slug="padel-group-onboard",
            description="Описание",
            city="Казань",
            sport=Sport.PADEL,
            is_active=True,
            type_prices={"group": 2000},
        )
        url = reverse("training_enroll", kwargs={"slug": training.slug})
        response = self.client.get(url, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("padel_strength"), response["Location"])

        set_padel_strength(self.player, Decimal("3.0"))
        after = self.client.get(url, secure=True)
        self.assertEqual(after.status_code, 200)
