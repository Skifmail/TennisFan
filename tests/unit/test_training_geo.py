"""География публичной страницы тренировок.

Заголовок называет Москву, города области и города активных тренировок.
Корт без тренировки в фильтр не попадает.
"""

from django.test import TestCase

from apps.core.geo import GeoRegion
from apps.core.models import GeoArea
from apps.courts.models import Court
from apps.training.geo import (
    advertised_training_areas,
    advertised_training_cities,
    courts_for_extra_city,
    courts_for_training_area,
    courts_for_training_city,
    extra_place_cities,
    extra_training_cities,
    filter_trainings_by_city,
    format_city_list,
    group_training_courts,
    is_moscow_city,
    oblast_area_for_city,
    public_training_cities,
    public_training_cities_label,
    resolve_training_list_geo,
    training_area_for_court,
    training_city_for_court,
    training_city_slug,
)
from apps.training.models import Training


def _make_court(
    *,
    name: str,
    slug: str,
    city: str,
    region: str = "",
    geo_area: GeoArea | None = None,
    district: str = "",
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
        district=district,
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

    def test_moscow_district_counts_as_moscow(self) -> None:
        area = GeoArea.objects.get(slug="yug")
        court = _make_court(
            name="Юг",
            slug="south-court",
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


class AdvertisedTrainingAreasTestCase(TestCase):
    """Районы Москвы и города области для выбора на странице тренировок."""

    def test_moscow_districts_then_oblast_cities(self) -> None:
        slugs = [area.slug for area in advertised_training_areas()]

        self.assertEqual(
            slugs,
            [
                "sever",
                "yug",
                "vostok",
                "zapad",
                "ramenskoe",
                "zhukovskiy",
                "voskresensk",
                "pavlovskiy-posad",
            ],
        )

    def test_hides_inactive_area(self) -> None:
        GeoArea.objects.filter(slug="yug").update(is_active=False)

        slugs = [area.slug for area in advertised_training_areas()]

        self.assertNotIn("yug", slugs)


class TrainingAreaForCourtTestCase(TestCase):
    """Привязка корта к району Москвы или городу области."""

    def test_uses_geo_area_when_set(self) -> None:
        area = GeoArea.objects.get(slug="yug")
        court = _make_court(
            name="Южный корт",
            slug="south-area-court",
            city="Москва",
            region=GeoRegion.MOSCOW,
            geo_area=area,
        )

        matched = training_area_for_court(court)

        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched.slug, "yug")

    def test_oblast_city_name_without_fk(self) -> None:
        court = _make_court(
            name="Раменский корт",
            slug="ram-area-court",
            city="Раменский",
        )

        matched = training_area_for_court(court)

        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched.slug, "ramenskoe")

    def test_moscow_district_text_without_fk(self) -> None:
        court = _make_court(
            name="Корт в ЮАО",
            slug="south-district-text",
            city="Москва",
            region=GeoRegion.MOSCOW,
            district="ЮАО",
        )

        matched = training_area_for_court(court)

        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched.slug, "yug")

    def test_neighborhood_does_not_match_cardinal_district(self) -> None:
        court = _make_court(
            name="Южное Бутово",
            slug="south-butovo",
            city="Москва",
            region=GeoRegion.MOSCOW,
            district="Южное Бутово",
        )

        self.assertIsNone(training_area_for_court(court))

    def test_moscow_without_district_is_unassigned(self) -> None:
        court = _make_court(
            name="Московский корт",
            slug="moscow-no-district",
            city="Москва",
            region=GeoRegion.MOSCOW,
        )

        self.assertIsNone(training_area_for_court(court))

    def test_foreign_city_is_ignored(self) -> None:
        court = _make_court(name="Казань", slug="kazan-area-court", city="Казань")

        self.assertIsNone(training_area_for_court(court))


class CourtsForTrainingAreaTestCase(TestCase):
    """Список кортов выбранного района или города."""

    def test_returns_only_selected_area(self) -> None:
        south = GeoArea.objects.get(slug="yug")
        ramenskoe = GeoArea.objects.get(slug="ramenskoe")
        _make_court(
            name="Корт Юг",
            slug="court-south-only",
            city="Москва",
            region=GeoRegion.MOSCOW,
            geo_area=south,
        )
        _make_court(
            name="Корт Раменское",
            slug="court-ram-only",
            city="Раменское",
            region=GeoRegion.MOSCOW_OBLAST,
            geo_area=ramenskoe,
        )

        names = [court.name for court in courts_for_training_area("yug")]

        self.assertEqual(names, ["Корт Юг"])

    def test_legacy_slug_maps_to_current_district(self) -> None:
        south = GeoArea.objects.get(slug="yug")
        _make_court(
            name="Корт Юг",
            slug="court-south-legacy",
            city="Москва",
            region=GeoRegion.MOSCOW,
            geo_area=south,
        )

        names = [court.name for court in courts_for_training_area("yugo-vostok")]

        self.assertEqual(names, ["Корт Юг"])

    def test_unknown_or_empty_slug_is_empty(self) -> None:
        self.assertEqual(courts_for_training_area(""), ())
        self.assertEqual(courts_for_training_area("unknown"), ())

    def test_extra_city_slug_returns_matching_courts(self) -> None:
        Training.objects.create(
            title="Ростов",
            slug="rostov-geo-training",
            description="Описание",
            city="Ростов-на-Дону",
            is_active=True,
            type_prices={"individual": 3000},
        )
        _make_court(name="Корт Ростов", slug="court-rostov-geo", city="Ростов-на-Дону")
        _make_court(name="Корт Казань", slug="court-kazan-geo", city="Казань")

        names = [
            court.name
            for court in courts_for_training_area(training_city_slug("Ростов-на-Дону"))
        ]

        self.assertEqual(names, ["Корт Ростов"])
        self.assertEqual(
            [court.name for court in courts_for_extra_city("Ростов-на-Дону")],
            ["Корт Ростов"],
        )

    def test_court_only_city_slug_is_empty_without_training(self) -> None:
        _make_court(name="Корт Адлер", slug="adler-geo-court", city="Адлер")

        self.assertEqual(courts_for_training_area(training_city_slug("Адлер")), ())

    def test_court_only_city_is_not_in_picker(self) -> None:
        _make_court(name="Корт Сочи", slug="sochi-place-court", city="Сочи")

        self.assertEqual(extra_training_cities(), [])
        self.assertEqual(extra_place_cities(), [])
        self.assertNotIn("Сочи", public_training_cities())


class ExtraTrainingCitiesTestCase(TestCase):
    """Города активных тренировок вне справочника Москвы и области."""

    def test_collects_active_cities_outside_catalog(self) -> None:
        Training.objects.create(
            title="Ростов",
            slug="rostov-extra-city",
            description="Описание",
            city="Ростов-на-Дону",
            is_active=True,
            type_prices={"individual": 3000},
        )
        Training.objects.create(
            title="Раменское",
            slug="ramenskoe-extra-city",
            description="Описание",
            city="Раменское",
            is_active=True,
            type_prices={"individual": 3000},
        )
        Training.objects.create(
            title="Сочи скрытая",
            slug="sochi-inactive-extra",
            description="Описание",
            city="Сочи",
            is_active=False,
            type_prices={"individual": 3000},
        )

        extras = extra_training_cities()

        self.assertEqual(extras, ["Ростов-на-Дону"])
        self.assertEqual(public_training_cities()[0], "Москва")
        self.assertEqual(public_training_cities()[-1], "Ростов-на-Дону")
        self.assertEqual(training_city_slug("Ростов-на-Дону"), "ростов-на-дону")

    def test_court_only_city_is_not_in_picker(self) -> None:
        _make_court(name="Корт Сочи", slug="sochi-place-court", city="Сочи")

        self.assertEqual(extra_training_cities(), [])
        self.assertEqual(extra_place_cities(), [])
        self.assertNotIn("Сочи", public_training_cities())

    def test_heading_collapses_many_extra_cities(self) -> None:
        Training.objects.create(
            title="Ростов",
            slug="rostov-heading-extra",
            description="Описание",
            city="Ростов-на-Дону",
            is_active=True,
            type_prices={"individual": 3000},
        )
        Training.objects.create(
            title="Сочи",
            slug="sochi-heading-extra",
            description="Описание",
            city="Сочи",
            is_active=True,
            type_prices={"individual": 3000},
        )

        label = public_training_cities_label()

        self.assertIn("Москва", label)
        self.assertIn("другие города", label)
        self.assertNotIn("Ростов-на-Дону", label)
        self.assertNotIn("Сочи", label)


class ResolveTrainingListGeoTestCase(TestCase):
    """Поле города: Москва по умолчанию, район только внутри неё."""

    def test_moscow_city_detection(self) -> None:
        self.assertTrue(is_moscow_city("Москва"))
        self.assertTrue(is_moscow_city(" москва "))
        self.assertFalse(is_moscow_city("Ростов-на-Дону"))
        self.assertFalse(is_moscow_city(""))

    def test_oblast_area_for_catalog_city(self) -> None:
        area = oblast_area_for_city("Раменское")

        self.assertIsNotNone(area)
        assert area is not None
        self.assertEqual(area.slug, "ramenskoe")
        self.assertIsNone(oblast_area_for_city("Москва"))
        self.assertIsNone(oblast_area_for_city("Ростов-на-Дону"))

    def test_default_is_moscow_with_zones(self) -> None:
        geo = resolve_training_list_geo()

        self.assertEqual(geo.city, "Москва")
        self.assertIsNone(geo.moscow_district)
        self.assertTrue(geo.show_moscow_zones)

    def test_moscow_keeps_district(self) -> None:
        geo = resolve_training_list_geo(city="Москва", area_slug="yug")

        self.assertEqual(geo.city, "Москва")
        self.assertIsNotNone(geo.moscow_district)
        assert geo.moscow_district is not None
        self.assertEqual(geo.moscow_district.slug, "yug")
        self.assertTrue(geo.show_moscow_zones)

    def test_other_city_hides_zones_and_drops_district(self) -> None:
        geo = resolve_training_list_geo(city="Ростов-на-Дону", area_slug="yug")

        self.assertEqual(geo.city, "Ростов-на-Дону")
        self.assertIsNone(geo.moscow_district)
        self.assertFalse(geo.show_moscow_zones)

    def test_legacy_oblast_slug_becomes_city(self) -> None:
        geo = resolve_training_list_geo(area_slug="ramenskoe")

        self.assertEqual(geo.city, "Раменское")
        self.assertFalse(geo.show_moscow_zones)

    def test_legacy_district_slug_stays_moscow(self) -> None:
        geo = resolve_training_list_geo(area_slug="yugo-vostok")

        self.assertEqual(geo.city, "Москва")
        self.assertIsNotNone(geo.moscow_district)
        assert geo.moscow_district is not None
        self.assertEqual(geo.moscow_district.slug, "yug")
        self.assertTrue(geo.show_moscow_zones)

    def test_courts_for_oblast_city(self) -> None:
        ramenskoe = GeoArea.objects.get(slug="ramenskoe")
        _make_court(
            name="Корт Раменское",
            slug="geo-ramenskoe-city",
            city="Раменское",
            region=GeoRegion.MOSCOW_OBLAST,
            geo_area=ramenskoe,
        )

        names = [court.name for court in courts_for_training_city("Раменское")]

        self.assertEqual(names, ["Корт Раменское"])


class MultiCityTrainingFilterTestCase(TestCase):
    """Тренировка сразу в нескольких городах видна в каждом из них."""

    def setUp(self) -> None:
        Training.objects.create(
            title="Тренировки по теннису в Москве, Раменском, Жуковском, Воскресенске",
            slug="multi-city-geo-training",
            description="Описание",
            city="Москва, Раменское, Жуковский, Воскресенск",
            is_active=True,
            type_prices={"individual": 3000},
        )

    def test_matches_each_listed_city(self) -> None:
        qs = Training.objects.filter(is_active=True)

        self.assertEqual(filter_trainings_by_city(qs, "Москва").count(), 1)
        self.assertEqual(filter_trainings_by_city(qs, "Раменское").count(), 1)
        self.assertEqual(filter_trainings_by_city(qs, "Жуковский").count(), 1)
        self.assertEqual(filter_trainings_by_city(qs, "Воскресенск").count(), 1)
        self.assertEqual(filter_trainings_by_city(qs, "Ростов-на-Дону").count(), 0)

    def test_combined_catalog_cities_are_not_extra(self) -> None:
        self.assertEqual(extra_training_cities(), [])
