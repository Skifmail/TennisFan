"""География публичной страницы тренировок.

Заголовок, текст и список кортов должны называть одни и те же города:
Москва и активные города области из справочника.
"""

from django.test import TestCase

from apps.core.geo import GeoRegion
from apps.core.models import GeoArea
from apps.courts.models import Court
from apps.training.geo import (
    advertised_training_cities,
    format_city_list,
    group_training_courts,
    training_city_for_court,
)


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


class AdvertisedTrainingCitiesTestCase(TestCase):
    """Состав рекламируемых городов тренировок."""

    def test_starts_with_moscow_then_oblast_catalog(self) -> None:
        cities = advertised_training_cities()

        self.assertEqual(cities[0], "Москва")
        self.assertEqual(
            cities[1:],
            ["Раменское", "Жуковский", "Воскресенск", "Павловский Посад"],
        )

    def test_hides_inactive_oblast_city(self) -> None:
        GeoArea.objects.filter(slug="voskresensk").update(is_active=False)

        self.assertNotIn("Воскресенск", advertised_training_cities())

    def test_format_joins_with_and(self) -> None:
        label = format_city_list(
            ["Москва", "Раменское", "Жуковский", "Воскресенск", "Павловский Посад"]
        )

        self.assertEqual(
            label,
            "Москва, Раменское, Жуковский, Воскресенск и Павловский Посад",
        )


class TrainingCityForCourtTestCase(TestCase):
    """Привязка корта к городу рекламируемой географии."""

    def test_moscow_zone_counts_as_moscow(self) -> None:
        area = GeoArea.objects.get(slug="yugo-vostok")
        court = _make_court(
            name="Юго-Восток",
            slug="se-court",
            city="Москва",
            region=GeoRegion.MOSCOW,
            geo_area=area,
        )

        self.assertEqual(training_city_for_court(court), "Москва")

    def test_oblast_geo_area_wins_over_city_field(self) -> None:
        area = GeoArea.objects.get(slug="zhukovskiy")
        court = _make_court(
            name="Жуковский корт",
            slug="zhuk-court",
            city="Москва",
            region=GeoRegion.MOSCOW_OBLAST,
            geo_area=area,
        )

        self.assertEqual(training_city_for_court(court), "Жуковский")

    def test_alias_ramenskiy_maps_to_ramenskoe(self) -> None:
        court = _make_court(
            name="Раменский корт",
            slug="ram-court",
            city="Раменский",
        )

        self.assertEqual(training_city_for_court(court), "Раменское")

    def test_foreign_city_is_ignored(self) -> None:
        court = _make_court(
            name="Казань",
            slug="kazan-court",
            city="Казань",
        )

        self.assertIsNone(training_city_for_court(court))


class GroupTrainingCourtsTestCase(TestCase):
    """Группы кортов: все рекламируемые города, без чужих площадок."""

    def test_keeps_empty_advertised_cities_and_drops_outsiders(self) -> None:
        voskresensk = GeoArea.objects.get(slug="voskresensk")
        _make_court(
            name="Воскресенск Арена",
            slug="vosk-arena",
            city="Воскресенск",
            region=GeoRegion.MOSCOW_OBLAST,
            geo_area=voskresensk,
        )
        _make_court(
            name="Чужой корт",
            slug="other-court",
            city="Казань",
        )

        groups = group_training_courts()
        by_city = {
            group.city: [court.name for court in group.courts] for group in groups
        }

        self.assertEqual(
            [group.city for group in groups],
            advertised_training_cities(),
        )
        self.assertEqual(by_city["Воскресенск"], ["Воскресенск Арена"])
        self.assertEqual(by_city["Москва"], [])
        self.assertNotIn("Казань", by_city)
        self.assertNotIn(
            "Чужой корт", [name for names in by_city.values() for name in names]
        )

    def test_city_filter_narrows_groups(self) -> None:
        groups = group_training_courts(city_filter="Жуковский")

        self.assertEqual([group.city for group in groups], ["Жуковский"])
