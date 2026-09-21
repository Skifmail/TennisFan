"""Адрес корта на карточке без повторённого населённого пункта."""

from django.test import TestCase
from django.urls import reverse

from apps.courts.geocoder import format_court_display_address
from apps.courts.models import Court


class FormatCourtDisplayAddressTestCase(TestCase):
    """Город не дублируется внутри строки адреса."""

    def test_strips_repeated_city_parts(self) -> None:
        address = format_court_display_address(
            "Видное",
            "Видное, Видное, Московская область, Ленинский городской округ, Зелёный переулок 19",
        )

        self.assertEqual(
            address,
            "Московская область, Ленинский городской округ, Зелёный переулок 19",
        )
        self.assertEqual(address.lower().count("видное"), 0)

    def test_keeps_street_when_city_is_not_in_address(self) -> None:
        self.assertEqual(
            format_court_display_address("Москва", "ул. Тестовая, 1"),
            "ул. Тестовая, 1",
        )

    def test_strips_leading_city_once(self) -> None:
        self.assertEqual(
            format_court_display_address("Москва", "Москва, ул. Тверская, 1"),
            "ул. Тверская, 1",
        )

    def test_collapses_consecutive_duplicates(self) -> None:
        self.assertEqual(
            format_court_display_address(
                "Сочи",
                "Сочи, Центральный район, Центральный район, ул. Ленина, 10",
            ),
            "Центральный район, ул. Ленина, 10",
        )

    def test_returns_empty_when_address_is_only_the_city(self) -> None:
        self.assertEqual(format_court_display_address("Видное", "Видное"), "")

    def test_ignores_city_prefix(self) -> None:
        self.assertEqual(
            format_court_display_address(
                "Видное",
                "г. Видное, Зелёный переулок, 19",
            ),
            "Зелёный переулок, 19",
        )


class CourtDisplayAddressTestCase(TestCase):
    """Карточка корта показывает адрес без тройного города."""

    def test_detail_does_not_repeat_city_in_address(self) -> None:
        court = Court.objects.create(
            name="Территория тенниса",
            slug="territoriya-tennisa",
            city="Видное",
            address=(
                "Видное, Видное, Московская область, "
                "Ленинский городской округ, Зелёный переулок 19"
            ),
            surface="хард",
            is_active=True,
        )

        response = self.client.get(
            reverse("court_detail", kwargs={"slug": court.slug}),
            secure=True,
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<dt>Населённый пункт</dt>", html=False)
        self.assertContains(response, "Видное")
        self.assertContains(response, "Зелёный переулок 19")
        self.assertNotIn("Видное, Видное", html)
        self.assertEqual(html.lower().count("видное"), 1)
