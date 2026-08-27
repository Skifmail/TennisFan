"""Публичная карточка корта: особенности и контакты."""

import re

from django.test import Client, TestCase
from django.urls import reverse

from apps.courts.models import Court


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
