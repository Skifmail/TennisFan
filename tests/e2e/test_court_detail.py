"""Публичная карточка корта: особенности и контакты."""

import re

from django.test import Client, TestCase
from django.urls import reverse

from apps.courts.models import Court
from apps.tournaments.models import Match
from tests.support.factories import make_player, make_tournament


class CourtDetailParkingTestCase(TestCase):
    """На странице корта парковка показывается одной опцией."""

    def setUp(self) -> None:
        self.client = Client()

    def test_detail_shows_car_parking_when_enabled(self) -> None:
        court = Court.objects.create(
            name="Корт с парковкой",
            slug="court-with-parking",
            city="Москва",
            address="ул. Тестовая, 1",
            surface="хард",
            has_parking=True,
            is_active=True,
        )

        response = self.client.get(
            reverse("court_detail", kwargs={"slug": court.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Автомобильная парковка")
        self.assertNotContains(response, "Парковка рядом")
        self.assertNotContains(response, "Парковка на территории")
        self.assertNotContains(response, "Парковка внутри")

    def test_detail_hides_parking_when_disabled(self) -> None:
        court = Court.objects.create(
            name="Корт без парковки",
            slug="court-without-parking",
            city="Москва",
            address="ул. Тестовая, 1",
            surface="хард",
            has_parking=False,
            is_active=True,
        )

        response = self.client.get(
            reverse("court_detail", kwargs={"slug": court.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Автомобильная парковка")


class CourtDetailWorkingHoursTestCase(TestCase):
    """Время работы стоит в блоке контактов сразу под телефоном."""

    def setUp(self) -> None:
        self.client = Client()

    def _create_court(
        self,
        *,
        phone: str = "",
        working_hours: str = "",
        description: str = "",
        name: str = "МЕГАСПОРТ-ТЕННИС",
        slug: str = "megasport-tennis",
        city: str = "Москва",
        address: str = "улица Миклухо-Маклая, вл4соор1",
        surface: str = "хард",
        is_active: bool = True,
    ) -> Court:
        return Court.objects.create(
            name=name,
            slug=slug,
            city=city,
            address=address,
            surface=surface,
            is_active=is_active,
            phone=phone,
            working_hours=working_hours,
            description=description,
        )

    def test_working_hours_appear_under_phone(self) -> None:
        court = self._create_court(
            phone="+7 495 221-06-93",
            working_hours="7:00 до 23:00",
        )

        response = self.client.get(
            reverse("court_detail", kwargs={"slug": court.slug}),
            secure=True,
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<dt>Время работы</dt>", html=False)
        self.assertContains(response, "7:00 до 23:00")
        self.assertIsNotNone(
            re.search(r"<dt>Телефон</dt>.*<dt>Время работы</dt>", html, flags=re.DOTALL)
        )

    def test_working_hours_are_not_a_separate_bottom_card(self) -> None:
        court = self._create_court(
            phone="+7 495 221-06-93",
            working_hours="7:00 до 23:00",
            description="",
        )

        response = self.client.get(
            reverse("court_detail", kwargs={"slug": court.slug}),
            secure=True,
        )
        html = response.content.decode()

        self.assertIn("court-detail__dl--striped", html)
        striped_start = html.find("court-detail__dl--striped")
        striped_end = html.find("</dl>", striped_start)
        striped_block = html[striped_start:striped_end]
        self.assertIn("Время работы", striped_block)
        self.assertNotIn(
            "Время работы",
            html[html.find("</dl>", striped_end) :],
        )

    def test_working_hours_hidden_when_empty(self) -> None:
        court = self._create_court(working_hours="")

        response = self.client.get(
            reverse("court_detail", kwargs={"slug": court.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<dt>Время работы</dt>", html=False)


class CourtDetailRecentMatchesTestCase(TestCase):
    """Лента матчей берёт и корт турнира, не только Match.court."""

    def test_shows_completed_matches_from_tournament_court(self) -> None:
        court = Court.objects.create(
            name="Теннисный центр Воскресенск",
            slug="tennisniy-centr-voskresensk",
            city="Воскресенск",
            address="ул. Тестовая, 1",
            surface="хард",
            is_active=True,
        )
        other = Court.objects.create(
            name="Другой корт",
            slug="other-court-matches",
            city="Москва",
            address="ул. Другая, 1",
            surface="хард",
            is_active=True,
        )
        player1 = make_player(email_suffix="c1", first_name="Иван")
        player2 = make_player(email_suffix="c2", first_name="Пётр")
        tournament = make_tournament(
            name="Турнир Воскресенск",
            slug="voskresensk-court-feed",
            court=court,
        )
        Match.objects.create(
            tournament=tournament,
            player1=player1,
            player2=player2,
            status=Match.MatchStatus.COMPLETED,
            player1_set1=6,
            player2_set1=4,
            player1_set2=6,
            player2_set2=3,
        )

        on_court = self.client.get(
            reverse("court_detail", kwargs={"slug": court.slug}),
            secure=True,
        )
        elsewhere = self.client.get(
            reverse("court_detail", kwargs={"slug": other.slug}),
            secure=True,
        )

        self.assertEqual(on_court.status_code, 200)
        self.assertContains(on_court, "Турнир Воскресенск")
        self.assertContains(on_court, "6:4 6:3")
        self.assertNotContains(on_court, "Матчи на этом корте пока не проводились.")
        self.assertContains(elsewhere, "Матчи на этом корте пока не проводились.")
        self.assertNotContains(elsewhere, "Турнир Воскресенск")
