"""Публичная страница тренировок: заголовок, текст и корты одной географии."""

from django.test import TestCase
from django.urls import reverse

from apps.core.geo import GeoRegion
from apps.core.models import GeoArea
from apps.courts.models import Court
from apps.training.forms import TrainingEnrollmentForm
from apps.training.models import Training


def _make_court(
    *,
    name: str,
    slug: str,
    city: str,
    region: str = "",
    geo_area: GeoArea | None = None,
) -> Court:
    """Создать активный корт с минимально нужными полями."""
    return Court.objects.create(
        name=name,
        slug=slug,
        city=city,
        address="ул. Тестовая, 1",
        surface="хард",
        region=region,
        geo_area=geo_area,
        is_active=True,
    )


class TrainingListGeographyTestCase(TestCase):
    """Заголовок, лид и список кортов называют одни и те же города."""

    def setUp(self) -> None:
        moscow_area = GeoArea.objects.get(slug="yugo-vostok")
        ramenskoe = GeoArea.objects.get(slug="ramenskoe")
        zhukovskiy = GeoArea.objects.get(slug="zhukovskiy")
        voskresensk = GeoArea.objects.get(slug="voskresensk")
        pavlovskiy = GeoArea.objects.get(slug="pavlovskiy-posad")
        _make_court(
            name="Корт ЮВАО",
            slug="court-uvao",
            city="Москва",
            region=GeoRegion.MOSCOW,
            geo_area=moscow_area,
        )
        _make_court(
            name="Корт Раменское",
            slug="court-ramenskoe",
            city="Раменское",
            region=GeoRegion.MOSCOW_OBLAST,
            geo_area=ramenskoe,
        )
        _make_court(
            name="Корт Жуковский",
            slug="court-zhukovskiy",
            city="Жуковский",
            region=GeoRegion.MOSCOW_OBLAST,
            geo_area=zhukovskiy,
        )
        _make_court(
            name="Корт Воскресенск",
            slug="court-voskresensk",
            city="Воскресенск",
            region=GeoRegion.MOSCOW_OBLAST,
            geo_area=voskresensk,
        )
        _make_court(
            name="Корт Павловский Посад",
            slug="court-pavlovskiy",
            city="Павловский Посад",
            region=GeoRegion.MOSCOW_OBLAST,
            geo_area=pavlovskiy,
        )
        _make_court(
            name="Корт Казань",
            slug="court-kazan",
            city="Казань",
        )

    def test_heading_lede_and_courts_list_same_cities(self) -> None:
        response = self.client.get(reverse("training_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тренировки для взрослых")
        self.assertContains(
            response,
            "Москва, Раменское, Жуковский, Воскресенск и Павловский Посад",
        )
        self.assertContains(response, "Корт ЮВАО")
        self.assertContains(response, "Корт Раменское")
        self.assertContains(response, "Корт Жуковский")
        self.assertContains(response, "Корт Воскресенск")
        self.assertContains(response, "Корт Павловский Посад")
        self.assertNotContains(response, "Корт Казань")

    def test_city_filter_options_match_advertised_cities(self) -> None:
        response = self.client.get(reverse("training_list"), secure=True)
        html = response.content.decode()

        self.assertIn('name="city"', html)
        for city in (
            "Москва",
            "Раменское",
            "Жуковский",
            "Воскресенск",
            "Павловский Посад",
        ):
            self.assertIn(f'value="{city}"', html)

    def test_long_city_court_list_shows_more_toggle(self) -> None:
        moscow_area = GeoArea.objects.get(slug="yugo-vostok")
        for index in range(1, 6):
            _make_court(
                name=f"Корт Москва {index}",
                slug=f"court-moscow-{index}",
                city="Москва",
                region=GeoRegion.MOSCOW,
                geo_area=moscow_area,
            )

        response = self.client.get(reverse("training_list"), secure=True)
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ещё...")
        self.assertContains(response, 'class="training-geo__more-toggle"')
        self.assertEqual(html.count('class="training-geo__more-toggle"'), 1)
        self.assertIn("Корт Москва 1", html)
        self.assertIn("Корт Москва 4", html)
        self.assertIn("Корт Москва 5", html)
        self.assertIn("training-geo__courts--extra", html)

    def test_short_city_court_list_hides_more_toggle(self) -> None:
        response = self.client.get(reverse("training_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Ещё...")
        self.assertNotContains(response, 'class="training-geo__more-toggle"')


class TrainingEnrollCourtChoicesTestCase(TestCase):
    """В заявке только корты рекламируемой географии."""

    def test_excludes_courts_outside_advertised_cities(self) -> None:
        _make_court(name="Корт Раменское", slug="enroll-ram", city="Раменское")
        _make_court(name="Корт Казань", slug="enroll-kazan", city="Казань")
        Training.objects.create(
            title="Тестовая тренировка",
            slug="geo-enroll-training",
            description="Описание",
            city="Раменское",
            is_active=True,
            type_prices={"individual": 3000},
        )

        form = TrainingEnrollmentForm()
        names = [court.name for court in form.fields["desired_court"].queryset]

        self.assertIn("Корт Раменское", names)
        self.assertNotIn("Корт Казань", names)
