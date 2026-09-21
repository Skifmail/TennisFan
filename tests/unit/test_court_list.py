"""Список кортов: город на карточке и поиск по названию."""

from django.test import TestCase
from django.urls import reverse

from apps.courts.models import Court


def _make_court(*, name: str, slug: str, city: str) -> Court:
    """Создать активный корт для списка."""
    return Court.objects.create(
        name=name,
        slug=slug,
        city=city,
        address="ул. Тестовая, 1",
        surface="хард",
        is_active=True,
    )


class CourtListCatalogTestCase(TestCase):
    """На карточках виден город, список ищется по названию."""

    def setUp(self) -> None:
        _make_court(
            name="Территория тенниса",
            slug="territoriya-tennisa",
            city="Видное",
        )
        _make_court(
            name="TennisCapital на Мечуринской",
            slug="tenniscapital",
            city="Москва",
        )

    def test_cards_show_city(self) -> None:
        response = self.client.get(reverse("court_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "court-card__city")
        self.assertContains(response, "Видное")
        self.assertContains(response, "Москва")
        self.assertContains(response, 'name="q"')
        self.assertContains(response, "Найти")
        self.assertContains(response, 'type="submit"')

    def test_search_by_court_name(self) -> None:
        response = self.client.get(
            reverse("court_list"),
            {"q": "Территория"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Территория тенниса")
        self.assertNotContains(response, "TennisCapital на Мечуринской")

    def test_search_keeps_city_filter(self) -> None:
        response = self.client.get(
            reverse("court_list"),
            {"q": "Tennis", "city": "Видное"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "TennisCapital на Мечуринской")
        self.assertNotContains(response, "Территория тенниса")
        self.assertContains(response, "Корты не найдены.")
