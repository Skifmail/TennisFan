"""Счётчики активных турниров и спаррингов города игрока на главной."""

from __future__ import annotations

from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.home_city_counters import (
    SPARRINGS_SEEN_COOKIE,
    TOURNAMENTS_SEEN_COOKIE,
    build_home_city_counters,
    parse_seen_at,
    pick_sparring_tab,
)
from apps.core.localities import primary_locality_name
from apps.sparring.models import (
    DoublesMatchKind,
    DoublesMatchRequest,
    DoublesMatchRequestStatus,
    SparringRequest,
)
from apps.tournaments.models import Tournament, TournamentStatus
from tests.support.factories import make_player, make_tournament

RAMENSKOE_QUERY = "city=%D0%A0%D0%B0%D0%BC%D0%B5%D0%BD%D1%81%D0%BA%D0%BE%D0%B5"


class PrimaryLocalityNameTests(SimpleTestCase):
    """Город для фильтра берётся из профиля без типа поселения и региона."""

    def test_strips_prefix_and_keeps_case(self) -> None:
        """«г. Раменское» превращается в «Раменское»."""
        self.assertEqual(primary_locality_name("г. Раменское"), "Раменское")

    def test_takes_first_city_and_skips_region(self) -> None:
        """Из списка берётся первый город, регион пропускается."""
        self.assertEqual(
            primary_locality_name("Московская область, Жуковский, Раменское"),
            "Жуковский",
        )

    def test_empty(self) -> None:
        """Пустой профиль даёт пустую строку."""
        self.assertEqual(primary_locality_name("  "), "")


class SeenCookieParsingTests(SimpleTestCase):
    """Разбор cookie просмотра списка устойчив к мусору."""

    def test_valid_timestamp(self) -> None:
        """Корректное Unix-время превращается в datetime UTC."""
        parsed = parse_seen_at("1700000000")
        assert parsed is not None
        self.assertEqual(int(parsed.timestamp()), 1700000000)

    def test_garbage_and_empty(self) -> None:
        """Мусор, ноль и пустое значение считаются отсутствием cookie."""
        for raw in (None, "", "abc", "0", "-5", "9" * 40):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_seen_at(raw))


class PickSparringTabTests(SimpleTestCase):
    """Счётчик спаррингов ведёт на вкладку, где есть что посмотреть."""

    def test_prefers_tab_with_new_requests(self) -> None:
        """Вкладка с новыми заявками важнее вкладки с одними старыми."""
        tab = pick_sparring_tab({"singles": (3, 0), "doubles": (1, 0), "team": (2, 1)})
        self.assertEqual(tab, "team")

    def test_falls_back_to_any_active_then_singles(self) -> None:
        """Без новых — первая непустая вкладка, иначе одиночные."""
        self.assertEqual(
            pick_sparring_tab({"singles": (0, 0), "doubles": (2, 0), "team": (0, 0)}),
            "doubles",
        )
        self.assertEqual(
            pick_sparring_tab({"singles": (0, 0), "doubles": (0, 0), "team": (0, 0)}),
            "singles",
        )


class HomeCityCountersTests(TestCase):
    """Подсчёт и ссылки счётчиков по городу профиля."""

    def setUp(self) -> None:
        self.now = timezone.now()
        self.author = make_player(email_suffix="author")
        self.viewer = make_player(email_suffix="viewer")
        self.viewer.city = "г. Раменское"
        self.viewer.save(update_fields=["city"])

        old_tournament = make_tournament(name="Старый", slug="old-t", city="Раменское")
        make_tournament(
            name="Новый",
            slug="new-t",
            city="Раменское",
            status=TournamentStatus.ACTIVE,
        )
        make_tournament(name="Чужой", slug="other-t", city="Сочи")
        make_tournament(
            name="Прошедший",
            slug="done-t",
            city="Раменское",
            status=TournamentStatus.COMPLETED,
        )
        Tournament.objects.filter(pk=old_tournament.pk).update(
            created_at=self.now - timedelta(days=30)
        )

        old_singles = SparringRequest.objects.create(
            player=self.author, city="Раменское", description="старая"
        )
        SparringRequest.objects.filter(pk=old_singles.pk).update(
            created_at=self.now - timedelta(days=30)
        )
        SparringRequest.objects.create(
            player=self.viewer, city="Раменское", description="своя"
        )
        SparringRequest.objects.create(
            player=self.author, city="Сочи", description="чужой город"
        )
        SparringRequest.objects.create(
            player=self.author,
            city="Раменское",
            description="закрыта",
            status=SparringRequest.Status.CLOSED,
        )
        DoublesMatchRequest.objects.create(
            created_by=self.author,
            city="Раменское",
            kind=DoublesMatchKind.CLASSIC,
            status=DoublesMatchRequestStatus.OPEN,
        )
        DoublesMatchRequest.objects.create(
            created_by=self.author,
            city="Раменское",
            kind=DoublesMatchKind.TEAM,
            status=DoublesMatchRequestStatus.CANCELLED,
        )

    def test_counts_only_active_in_city_and_marks_recent_as_new(self) -> None:
        """Без cookie новыми считаются записи за неделю, свои заявки — нет."""
        counters = build_home_city_counters(
            city="Раменское",
            tournaments_seen_at=None,
            sparrings_seen_at=None,
            exclude_player_id=self.viewer.pk,
            now=self.now,
        )

        self.assertEqual(counters.tournaments.total, 2)
        self.assertEqual(counters.tournaments.new, 1)
        self.assertEqual(counters.sparrings.total, 3)
        self.assertEqual(counters.sparrings.new, 1)
        self.assertEqual(
            counters.tournaments.url, f"{reverse('tournament_list')}?{RAMENSKOE_QUERY}"
        )
        self.assertEqual(
            counters.sparrings.url,
            f"{reverse('sparring_list')}?type=doubles&{RAMENSKOE_QUERY}",
        )

    def test_seen_cookie_resets_new_count(self) -> None:
        """После просмотра списка старые записи перестают быть новыми."""
        later = self.now + timedelta(seconds=1)
        counters = build_home_city_counters(
            city="Раменское",
            tournaments_seen_at=later,
            sparrings_seen_at=later,
            now=later + timedelta(seconds=1),
        )
        self.assertEqual(counters.tournaments.new, 0)
        self.assertEqual(counters.sparrings.new, 0)
        self.assertEqual(counters.tournaments.total, 2)

    def test_without_city_counts_whole_platform(self) -> None:
        """Без города счётчики считаются по всей платформе."""
        counters = build_home_city_counters(
            city="",
            tournaments_seen_at=None,
            sparrings_seen_at=None,
            now=self.now,
        )
        self.assertEqual(counters.tournaments.total, 3)
        self.assertEqual(counters.tournaments.url, reverse("tournament_list"))
        self.assertEqual(counters.sparrings.total, 4)

    def test_home_renders_city_counters_with_badges(self) -> None:
        """Главная показывает оба счётчика, бейджи и ссылки на город игрока."""
        self.client.force_login(self.viewer.user)
        response = self.client.get(reverse("home"), secure=True)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('data-home-city-counter="tournaments"', content)
        self.assertIn('data-home-city-counter="sparrings"', content)
        self.assertIn("активных спарринга", content)
        self.assertIn('aria-label="1 новый турнир"', content)
        self.assertIn('aria-label="1 новый спарринг"', content)
        self.assertIn(RAMENSKOE_QUERY, content)

    def test_list_pages_set_seen_cookies_and_hide_badges(self) -> None:
        """Открытие списков ставит cookie, и бейджи на главной гаснут."""
        tournaments = self.client.get(reverse("tournament_list"), secure=True)
        sparrings = self.client.get(reverse("sparring_list"), secure=True)

        self.assertIn(TOURNAMENTS_SEEN_COOKIE, tournaments.cookies)
        self.assertIn(SPARRINGS_SEEN_COOKIE, sparrings.cookies)

        future = str(int((timezone.now() + timedelta(minutes=1)).timestamp()))
        self.client.cookies[TOURNAMENTS_SEEN_COOKIE] = future
        self.client.cookies[SPARRINGS_SEEN_COOKIE] = future
        home = self.client.get(reverse("home"), secure=True)
        self.assertNotContains(home, "hero-metric__badge")

    def test_anonymous_home_links_to_unfiltered_lists(self) -> None:
        """Гость видит общие ссылки без фильтра города."""
        response = self.client.get(reverse("home"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, f'href="{reverse("tournament_list")}" class="hero-metric'
        )
