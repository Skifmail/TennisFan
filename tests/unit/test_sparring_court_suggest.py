"""Подсказки кортов в поле места заявки на спарринг."""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.courts.models import Court
from apps.sparring.forms import DoublesMatchRequestForm, SparringRequestForm
from tests.support.factories import make_user


def _court(*, name: str, slug: str, city: str, **kwargs: object) -> Court:
    """Создать корт каталога."""
    return Court.objects.create(
        name=name,
        slug=slug,
        city=city,
        address="ул. Тестовая, 1",
        **kwargs,
    )


class SparringCourtSuggestTests(TestCase):
    """Список кортов учитывает город, название и вид спорта."""

    def setUp(self) -> None:
        self.user = make_user(email="court-suggest@test.local")
        self.client.force_login(self.user)
        _court(name="Альфа", slug="alpha-msk", city="Москва")
        _court(name="Альфа Юг", slug="alpha-south", city="Сочи")
        _court(name="Бета", slug="beta-msk", city="Москва", is_active=False)
        _court(
            name="Падел Альфа",
            slug="padel-alpha",
            city="Москва",
            venue_sport="padel",
        )

    def test_city_without_query_returns_active_tennis_courts(self) -> None:
        """По городу подтягиваются активные теннисные корты, без чужого города."""
        response = self.client.get(
            reverse("sparring_court_suggest"),
            {"city": "Москва", "sport": "tennis"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        names = [item["name"] for item in response.json()["courts"]]
        self.assertEqual(names, ["Альфа"])

    def test_query_narrows_by_name_inside_city(self) -> None:
        """Текст «Альфа» не подмешивает корт из другого города."""
        response = self.client.get(
            reverse("sparring_court_suggest"),
            {"q": "Альфа", "city": "г. Москва", "sport": "tennis"},
            secure=True,
        )

        names = [item["name"] for item in response.json()["courts"]]
        self.assertEqual(names, ["Альфа"])
        self.assertEqual(response.json()["courts"][0]["address"], "ул. Тестовая, 1")

    def test_padel_sport_returns_padel_court(self) -> None:
        """Для падела в списке площадка падела, а не теннисный корт."""
        response = self.client.get(
            reverse("sparring_court_suggest"),
            {"q": "Альфа", "city": "Москва", "sport": "padel"},
            secure=True,
        )

        names = [item["name"] for item in response.json()["courts"]]
        self.assertEqual(names, ["Падел Альфа"])

    def test_forms_mark_location_field_for_suggest(self) -> None:
        """Одиночная и парная формы отдают адресу подсказок в поле места."""
        singles = SparringRequestForm()
        doubles = DoublesMatchRequestForm()
        url = reverse("sparring_court_suggest")
        self.assertEqual(
            singles.fields["preferred_location"].widget.attrs["data-court-suggest"],
            url,
        )
        self.assertEqual(
            doubles.fields["preferred_location"].widget.attrs["data-court-suggest"],
            url,
        )
