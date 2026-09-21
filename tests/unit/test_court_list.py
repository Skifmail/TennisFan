"""Список кортов: город на карточке и поиск по названию."""

from django.test import TestCase
from django.urls import reverse

from apps.courts.models import Court
from apps.courts.views import format_courts_count


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


class FormatCourtsCountTestCase(TestCase):
    """Склонение «корт» в шапке каталога."""

    def test_russian_plural_forms(self) -> None:
        self.assertEqual(format_courts_count(0), "0 кортов")
        self.assertEqual(format_courts_count(1), "1 корт")
        self.assertEqual(format_courts_count(2), "2 корта")
        self.assertEqual(format_courts_count(5), "5 кортов")
        self.assertEqual(format_courts_count(11), "11 кортов")
        self.assertEqual(format_courts_count(21), "21 корт")
        self.assertEqual(format_courts_count(22), "22 корта")


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
        self.assertContains(response, "courts-masthead")
        self.assertContains(response, "2 корта")
        self.assertContains(response, "Подать заявку")
        self.assertNotContains(response, "Подать заявку на добавление корта")
        self.assertNotContains(response, "Сбросить фильтры")

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
        self.assertContains(response, "courts-empty__reset")
        self.assertContains(response, "Сбросить фильтры")
