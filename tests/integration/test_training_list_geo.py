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
        moscow_area = GeoArea.objects.get(slug="yug")
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

    def test_heading_and_area_picker_without_court_dump(self) -> None:
        response = self.client.get(reverse("training_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тренировки для взрослых")
        self.assertContains(
            response,
            "Москва, Раменское, Жуковский, Воскресенск, Павловский Посад и Казань",
        )
        self.assertContains(response, "Где вам удобно?")
        self.assertContains(response, "Юг")
        self.assertContains(response, "Раменское")
        self.assertContains(response, "Другие города")
        self.assertContains(response, "Казань")
        self.assertNotContains(response, 'name="city"')
        self.assertNotContains(response, "Корт ЮВАО")
        self.assertNotContains(response, "Корт Раменское")
        self.assertNotContains(response, "Корт Казань")
        self.assertNotContains(response, "Ещё...")
        self.assertNotContains(response, 'class="training-geo__more-toggle"')

    def test_selected_area_shows_only_its_courts(self) -> None:
        response = self.client.get(
            reverse("training_list"),
            {"area": "yug"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Корт ЮВАО")
        self.assertNotContains(response, "Корт Раменское")
        self.assertNotContains(response, "Корт Казань")
        self.assertContains(response, "data-court-search")

    def test_legacy_area_slug_shows_current_district_courts(self) -> None:
        response = self.client.get(
            reverse("training_list"),
            {"area": "yugo-vostok"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Корт ЮВАО")
        self.assertNotContains(response, "Корт Раменское")

    def test_unknown_area_hides_courts(self) -> None:
        response = self.client.get(
            reverse("training_list"),
            {"area": "unknown"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Корт ЮВАО")
        self.assertNotContains(response, "Корт Раменское")


class TrainingListExtraCitiesTestCase(TestCase):
    """Города активных тренировок вне Москвы появляются в фильтре."""

    def setUp(self) -> None:
        _make_court(name="Корт Ростов", slug="court-rostov-list", city="Ростов-на-Дону")
        _make_court(name="Корт Казань", slug="court-kazan-list", city="Казань")
        Training.objects.create(
            title="Тренировка в Ростове",
            slug="rostov-list-training",
            description="Описание",
            city="Ростов-на-Дону",
            is_active=True,
            type_prices={"individual": 3000},
        )
        Training.objects.create(
            title="Тренировка в Москве",
            slug="moscow-list-training",
            description="Описание",
            city="Москва",
            is_active=True,
            type_prices={"individual": 3000},
        )

    def test_heading_and_picker_include_training_city(self) -> None:
        response = self.client.get(reverse("training_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Другие города")
        self.assertContains(response, "Ростов-на-Дону")
        self.assertContains(response, "Казань")
        self.assertContains(
            response,
            "Москва, Раменское, Жуковский, Воскресенск, Павловский Посад, Казань и Ростов-на-Дону",
        )
        self.assertNotContains(response, "Корт Ростов")
        self.assertNotContains(response, "Корт Казань")

    def test_selected_extra_city_shows_courts_and_local_trainings(self) -> None:
        response = self.client.get(
            reverse("training_list"),
            {"area": "ростов-на-дону"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Корт Ростов")
        self.assertNotContains(response, "Корт Казань")
        self.assertContains(response, "Тренировка в Ростове")
        self.assertNotContains(response, "Тренировка в Москве")


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

    def test_includes_courts_from_training_cities_outside_moscow(self) -> None:
        _make_court(name="Корт Ростов", slug="enroll-rostov", city="Ростов-на-Дону")
        _make_court(name="Корт Казань", slug="enroll-kazan-extra", city="Казань")
        Training.objects.create(
            title="Тренировка в Ростове",
            slug="geo-enroll-rostov",
            description="Описание",
            city="Ростов-на-Дону",
            is_active=True,
            type_prices={"individual": 3000},
        )

        form = TrainingEnrollmentForm()
        names = [court.name for court in form.fields["desired_court"].queryset]

        self.assertIn("Корт Ростов", names)
        self.assertNotIn("Корт Казань", names)
