"""Тесты справочника зон и посадочных страниц турниров.

Проверяют распознавание зоны в названии турнира (в продакшене зона зашита
только туда), разбор адресов лендингов и построение канонических ссылок.
"""

from django.http import Http404
from django.test import TestCase

from apps.core.geo import GeoRegion, normalize_geo_text, region_to_slug
from apps.core.models import GeoArea
from apps.tournaments.landing import (
    TournamentLanding,
    geo_area_choices,
    resolve_landing,
)
from apps.tournaments.models import TournamentVariant


class NormalizeGeoTextTestCase(TestCase):
    """Приведение названий к сравнимому виду."""

    def test_replaces_non_breaking_hyphen(self) -> None:
        self.assertEqual(normalize_geo_text("Юго\u2011Восточный"), "юго-восточный")

    def test_collapses_spaces_and_case(self) -> None:
        self.assertEqual(
            normalize_geo_text("  Павловский   Посад "), "павловский посад"
        )

    def test_handles_empty_input(self) -> None:
        self.assertEqual(normalize_geo_text(""), "")


class GeoAreaSeedTestCase(TestCase):
    """Начальное наполнение справочника миграцией."""

    def test_four_moscow_zones_are_advertised(self) -> None:
        zones = GeoArea.objects.filter(region=GeoRegion.MOSCOW, is_advertised=True)

        self.assertEqual(zones.count(), 4)
        self.assertEqual(
            sorted(zones.values_list("slug", flat=True)),
            ["severo-vostok", "severo-zapad", "yugo-vostok", "yugo-zapad"],
        )

    def test_oblast_cities_are_separate_areas(self) -> None:
        cities = GeoArea.objects.filter(region=GeoRegion.MOSCOW_OBLAST)

        self.assertEqual(
            sorted(cities.values_list("slug", flat=True)),
            ["pavlovskiy-posad", "ramenskoe", "voskresensk", "zhukovskiy"],
        )


class ResolveFromNameTestCase(TestCase):
    """Распознавание зоны по названию турнира."""

    def test_recognizes_production_tournament_names(self) -> None:
        cases = {
            "Любительский Северо-Восточный кубок Москвы по теннису": "severo-vostok",
            "Любительский Северо-Западный кубок Москвы по теннису": "severo-zapad",
            "Любительский Юго-Восточный кубок Москвы по теннису": "yugo-vostok",
            "Любительский Юго-Западный кубок Москвы по теннису": "yugo-zapad",
        }
        for name, expected_slug in cases.items():
            with self.subTest(name=name):
                area = GeoArea.resolve_from_name(name, region=GeoRegion.MOSCOW)
                self.assertIsNotNone(area)
                self.assertEqual(area.slug, expected_slug)

    def test_recognizes_non_breaking_hyphen(self) -> None:
        area = GeoArea.resolve_from_name(
            "Любительский Юго\u2011Восточный кубок Москвы", region=GeoRegion.MOSCOW
        )

        self.assertIsNotNone(area)
        self.assertEqual(area.slug, "yugo-vostok")

    def test_prefers_longer_alias(self) -> None:
        GeoArea.objects.create(
            region=GeoRegion.MOSCOW,
            name="Восток",
            slug="vostok-test",
            sort_order=99,
        )

        area = GeoArea.resolve_from_name(
            "Любительский Юго-Восточный кубок", region=GeoRegion.MOSCOW
        )

        self.assertEqual(area.slug, "yugo-vostok")

    def test_ignores_inactive_area(self) -> None:
        GeoArea.objects.filter(slug="yugo-vostok").update(is_active=False)

        self.assertIsNone(
            GeoArea.resolve_from_name(
                "Любительский Юго-Восточный кубок", region=GeoRegion.MOSCOW
            )
        )

    def test_returns_none_for_empty_text(self) -> None:
        self.assertIsNone(GeoArea.resolve_from_name(""))


class ResolveLandingTestCase(TestCase):
    """Разбор адреса посадочной страницы."""

    def test_empty_arguments_give_general_catalog(self) -> None:
        landing = resolve_landing()

        self.assertFalse(landing.is_filtered)
        self.assertEqual(landing.url, "/tournaments/")

    def test_region_only(self) -> None:
        landing = resolve_landing("moscow")

        self.assertEqual(landing.region, GeoRegion.MOSCOW)
        self.assertIsNone(landing.area)
        self.assertEqual(landing.url, "/tournaments/moscow/")

    def test_region_with_area_and_variant(self) -> None:
        landing = resolve_landing("moscow", "yugo-vostok", "doubles")

        self.assertEqual(landing.area.slug, "yugo-vostok")
        self.assertEqual(landing.variant, TournamentVariant.DOUBLES)
        self.assertEqual(landing.url, "/tournaments/moscow/yugo-vostok/doubles/")

    def test_area_without_region_derives_region(self) -> None:
        landing = resolve_landing(None, "ramenskoe")

        self.assertEqual(landing.region, GeoRegion.MOSCOW_OBLAST)
        self.assertEqual(landing.url, "/tournaments/moskovskaya-oblast/ramenskoe/")

    def test_unknown_region_is_not_found(self) -> None:
        with self.assertRaises(Http404):
            resolve_landing("sochi")

    def test_unknown_variant_is_not_found(self) -> None:
        with self.assertRaises(Http404):
            resolve_landing("moscow", None, "mixed")

    def test_area_from_another_region_is_not_found(self) -> None:
        with self.assertRaises(Http404):
            resolve_landing("moscow", "ramenskoe")

    def test_inactive_area_is_not_found(self) -> None:
        GeoArea.objects.filter(slug="yugo-vostok").update(is_active=False)

        with self.assertRaises(Http404):
            resolve_landing("moscow", "yugo-vostok")


class LandingHeadingTestCase(TestCase):
    """Заголовки посадочных страниц."""

    def test_general_catalog(self) -> None:
        self.assertEqual(TournamentLanding().heading, "Любительские турниры по теннису")

    def test_region_and_variant(self) -> None:
        landing = resolve_landing("moscow", None, "doubles")

        self.assertEqual(landing.heading, "Парные турниры по теннису в Москве")

    def test_area_is_added_to_heading(self) -> None:
        landing = resolve_landing("moscow", "yugo-vostok", "singles")

        self.assertEqual(
            landing.heading, "Одиночные турниры по теннису в Москве, Юго-Восток"
        )


class GeoAreaChoicesTestCase(TestCase):
    """Список площадок для фильтра."""

    def test_filters_by_region_and_exposes_region_slug(self) -> None:
        areas = geo_area_choices(GeoRegion.MOSCOW)

        self.assertEqual(len(areas), 4)
        for area in areas:
            self.assertEqual(area.region_slug, region_to_slug(GeoRegion.MOSCOW))

    def test_without_region_returns_all_active_areas(self) -> None:
        self.assertEqual(len(geo_area_choices()), 8)

    def test_inactive_area_is_hidden(self) -> None:
        GeoArea.objects.filter(slug="voskresensk").update(is_active=False)

        slugs = [area.slug for area in geo_area_choices()]

        self.assertNotIn("voskresensk", slugs)
