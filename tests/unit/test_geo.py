"""Тесты справочника районов и посадочных страниц турниров.

Проверяют распознавание района в названии турнира (в продакшене район зашит
только туда), разбор адресов лендингов и построение канонических ссылок.
"""

from datetime import date
from importlib import import_module

from django.apps import apps
from django.http import Http404
from django.test import Client, TestCase
from django.urls import reverse

from apps.core.geo import (
    GeoRegion,
    area_label,
    canonical_area_slug,
    normalize_geo_text,
    region_to_slug,
)
from apps.core.models import GeoArea
from apps.tournaments.landing import (
    TournamentLanding,
    build_home_filter_chips,
    build_tournament_filter_chips,
    geo_area_choices,
    resolve_landing,
)
from apps.tournaments.models import Tournament, TournamentVariant


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


class AreaLabelTestCase(TestCase):
    """Подпись единицы деления региона."""

    def test_moscow_uses_district(self) -> None:
        self.assertEqual(area_label(GeoRegion.MOSCOW), "район")

    def test_oblast_uses_city(self) -> None:
        self.assertEqual(area_label(GeoRegion.MOSCOW_OBLAST), "город")


class CanonicalAreaSlugTestCase(TestCase):
    """Старые диагональные слаги ведут на стороны света."""

    def test_maps_legacy_diagonal_slugs(self) -> None:
        self.assertEqual(canonical_area_slug("yugo-vostok"), "yug")
        self.assertEqual(canonical_area_slug("yugo-zapad"), "zapad")
        self.assertEqual(canonical_area_slug("severo-vostok"), "vostok")
        self.assertEqual(canonical_area_slug("severo-zapad"), "sever")

    def test_keeps_current_slugs(self) -> None:
        self.assertEqual(canonical_area_slug("yug"), "yug")
        self.assertEqual(canonical_area_slug("ramenskoe"), "ramenskoe")


class GeoAreaSeedTestCase(TestCase):
    """Начальное наполнение справочника миграцией."""

    def test_four_moscow_districts_are_advertised(self) -> None:
        districts = GeoArea.objects.filter(region=GeoRegion.MOSCOW, is_advertised=True)

        self.assertEqual(districts.count(), 4)
        self.assertEqual(
            sorted(districts.values_list("slug", flat=True)),
            ["sever", "vostok", "yug", "zapad"],
        )
        self.assertEqual(
            set(districts.values_list("name", flat=True)),
            {"Север", "Юг", "Восток", "Запад"},
        )

    def test_oblast_cities_are_separate_areas(self) -> None:
        cities = GeoArea.objects.filter(region=GeoRegion.MOSCOW_OBLAST)

        self.assertEqual(
            sorted(cities.values_list("slug", flat=True)),
            ["pavlovskiy-posad", "ramenskoe", "voskresensk", "zhukovskiy"],
        )


class RenameMoscowDistrictsMergeTestCase(TestCase):
    """Миграция 0033 сливает админский район со старой зоной."""

    def test_merges_when_target_slug_already_exists(self) -> None:
        migration = import_module("apps.core.migrations.0033_moscow_cardinal_districts")

        canonical = GeoArea.objects.get(slug="sever")
        leftover = GeoArea.objects.create(
            region=GeoRegion.MOSCOW,
            name="Северо-Запад",
            slug="severo-zapad",
            aliases="СЗАО",
            sort_order=99,
            is_active=True,
            is_advertised=True,
        )
        tournament = Tournament.objects.create(
            name="Старый северо-запад",
            slug="old-northwest",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
            geo_area=leftover,
        )

        migration.rename_moscow_districts(apps, None)

        tournament.refresh_from_db()
        merged = GeoArea.objects.get(slug="sever")
        self.assertFalse(GeoArea.objects.filter(slug="severo-zapad").exists())
        self.assertEqual(GeoArea.objects.filter(slug="sever").count(), 1)
        self.assertEqual(tournament.geo_area_id, canonical.pk)
        self.assertEqual(merged.pk, canonical.pk)
        self.assertIn("СЗАО", merged.aliases)
        self.assertIn("Северо-Запад", merged.aliases)

    def test_rename_is_idempotent(self) -> None:
        migration = import_module("apps.core.migrations.0033_moscow_cardinal_districts")

        migration.rename_moscow_districts(apps, None)
        migration.rename_moscow_districts(apps, None)

        self.assertEqual(
            sorted(
                GeoArea.objects.filter(region=GeoRegion.MOSCOW).values_list(
                    "slug", flat=True
                )
            ),
            ["sever", "vostok", "yug", "zapad"],
        )


class ResolveFromNameTestCase(TestCase):
    """Распознавание района по названию турнира."""

    def test_recognizes_cardinal_tournament_names(self) -> None:
        cases = {
            "Любительский Северный кубок Москвы по теннису": "sever",
            "Любительский Южный кубок Москвы по теннису": "yug",
            "Любительский Восточный кубок Москвы по теннису": "vostok",
            "Любительский Западный кубок Москвы по теннису": "zapad",
        }
        for name, expected_slug in cases.items():
            with self.subTest(name=name):
                area = GeoArea.resolve_from_name(name, region=GeoRegion.MOSCOW)
                self.assertIsNotNone(area)
                self.assertEqual(area.slug, expected_slug)

    def test_recognizes_legacy_diagonal_tournament_names(self) -> None:
        cases = {
            "Любительский Северо-Восточный кубок Москвы по теннису": "vostok",
            "Любительский Северо-Западный кубок Москвы по теннису": "sever",
            "Любительский Юго-Восточный кубок Москвы по теннису": "yug",
            "Любительский Юго-Западный кубок Москвы по теннису": "zapad",
        }
        for name, expected_slug in cases.items():
            with self.subTest(name=name):
                area = GeoArea.resolve_from_name(name, region=GeoRegion.MOSCOW)
                self.assertIsNotNone(area)
                self.assertEqual(area.slug, expected_slug)

    def test_recognizes_okrug_abbreviations(self) -> None:
        cases = {
            "ЮВАО": "yug",
            "ЮЗАО": "zapad",
            "СВАО": "vostok",
            "СЗАО": "sever",
            "ЮАО": "yug",
            "САО": "sever",
            "ВАО": "vostok",
            "ЗАО": "zapad",
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
        self.assertEqual(area.slug, "yug")

    def test_prefers_longer_alias(self) -> None:
        area = GeoArea.resolve_from_name(
            "Любительский Юго-Восточный кубок", region=GeoRegion.MOSCOW
        )

        self.assertEqual(area.slug, "yug")

    def test_ignores_inactive_area(self) -> None:
        GeoArea.objects.filter(slug="yug").update(is_active=False)

        self.assertIsNone(
            GeoArea.resolve_from_name(
                "Любительский Южный кубок", region=GeoRegion.MOSCOW
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
        landing = resolve_landing("moscow", "yug", "doubles")

        self.assertEqual(landing.area.slug, "yug")
        self.assertEqual(landing.variant, TournamentVariant.DOUBLES)
        self.assertEqual(landing.url, "/tournaments/moscow/yug/doubles/")

    def test_legacy_diagonal_slugs_resolve_to_cardinals(self) -> None:
        cases = {
            "yugo-vostok": "yug",
            "yugo-zapad": "zapad",
            "severo-vostok": "vostok",
            "severo-zapad": "sever",
        }
        for legacy, current in cases.items():
            with self.subTest(legacy=legacy):
                landing = resolve_landing("moscow", legacy)
                self.assertEqual(landing.area.slug, current)
                self.assertEqual(landing.url, f"/tournaments/moscow/{current}/")

    def test_legacy_slug_works_if_row_not_renamed_yet(self) -> None:
        GeoArea.objects.filter(slug="yug").update(slug="yugo-vostok")

        landing = resolve_landing("moscow", "yugo-vostok")

        self.assertEqual(landing.area.slug, "yugo-vostok")

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
        GeoArea.objects.filter(slug="yug").update(is_active=False)

        with self.assertRaises(Http404):
            resolve_landing("moscow", "yug")


class LandingHeadingTestCase(TestCase):
    """Заголовки посадочных страниц."""

    def test_general_catalog(self) -> None:
        self.assertEqual(TournamentLanding().heading, "Любительские турниры по теннису")

    def test_region_and_variant(self) -> None:
        landing = resolve_landing("moscow", None, "doubles")

        self.assertEqual(landing.heading, "Парные турниры по теннису в Москве")

    def test_area_is_added_to_heading(self) -> None:
        landing = resolve_landing("moscow", "yug", "singles")

        self.assertEqual(landing.heading, "Одиночные турниры по теннису в Москве, Юг")


class GeoAreaChoicesTestCase(TestCase):
    """Список площадок для фильтра."""

    def test_filters_by_region_and_exposes_region_slug(self) -> None:
        areas = geo_area_choices(GeoRegion.MOSCOW)

        self.assertEqual(len(areas), 4)
        self.assertEqual(
            [area.name for area in areas],
            ["Север", "Юг", "Восток", "Запад"],
        )
        for area in areas:
            self.assertEqual(area.region_slug, region_to_slug(GeoRegion.MOSCOW))

    def test_without_region_returns_all_active_areas(self) -> None:
        self.assertEqual(len(geo_area_choices()), 8)

    def test_inactive_area_is_hidden(self) -> None:
        GeoArea.objects.filter(slug="voskresensk").update(is_active=False)

        slugs = [area.slug for area in geo_area_choices()]

        self.assertNotIn("voskresensk", slugs)


class CatalogFilterCopyTestCase(TestCase):
    """Публичный каталог называет площадку районом, не зоной."""

    def test_filter_label_uses_district(self) -> None:
        response = Client().get(reverse("tournament_list"), secure=True)

        self.assertContains(response, "Район или город")
        self.assertContains(response, "Все районы и города")
        self.assertContains(response, "Север")
        self.assertContains(response, "Юг")
        self.assertNotContains(response, "Зона или город")
        self.assertNotContains(response, "Юго-Восток")


class TournamentFilterChipsTestCase(TestCase):
    """Подписи активных фильтров для компактной мобильной панели."""

    def test_empty_when_no_filters_selected(self) -> None:
        self.assertEqual(
            build_tournament_filter_chips(
                current_region="",
                region_opts=[{"slug": "moscow", "label": "Москва"}],
                current_area_name="",
                current_variant="",
                variant_opts=[{"slug": "singles", "label": "Одиночный"}],
                current_city="",
                current_category="",
                category_choices=(("amateur", "Любитель"),),
                current_status="",
                is_archive=False,
                club_filter="",
                club_choices=(("club-a", "Клуб А"),),
            ),
            [],
        )

    def test_collects_labels_in_form_order(self) -> None:
        chips = build_tournament_filter_chips(
            current_region="moscow",
            region_opts=[{"slug": "moscow", "label": "Москва"}],
            current_area_name="Юг",
            current_variant="singles",
            variant_opts=[{"slug": "singles", "label": "Одиночный"}],
            current_city="Химки",
            current_category="amateur",
            category_choices=(("amateur", "Любитель"),),
            current_status="upcoming",
            is_archive=False,
            club_filter="__platform__",
            club_choices=(("club-a", "Клуб А"),),
        )

        self.assertEqual(
            chips,
            [
                "Москва",
                "Юг",
                "Одиночный",
                "Химки",
                "Любитель",
                "Предстоящие",
                "TennisFan",
            ],
        )

    def test_archive_omits_status_chip(self) -> None:
        chips = build_tournament_filter_chips(
            current_region="",
            region_opts=[],
            current_area_name="",
            current_variant="",
            variant_opts=[],
            current_city="",
            current_category="",
            category_choices=(),
            current_status="completed",
            is_archive=True,
            club_filter="__club_only__",
            club_choices=(),
        )

        self.assertEqual(chips, ["Только клубные"])

    def test_named_club_uses_club_title(self) -> None:
        chips = build_tournament_filter_chips(
            current_region="",
            region_opts=[],
            current_area_name="",
            current_variant="",
            variant_opts=[],
            current_city="",
            current_category="",
            category_choices=(),
            current_status="",
            is_archive=False,
            club_filter="club-a",
            club_choices=(("club-a", "Клуб А"),),
        )

        self.assertEqual(chips, ["Клуб А"])


class HomeFilterChipsTestCase(TestCase):
    """Подписи активных фильтров блока турниров на главной."""

    def test_empty_when_no_filters_selected(self) -> None:
        self.assertEqual(
            build_home_filter_chips(
                city="",
                category="",
                gender="",
                duration="",
                club_filter="",
                category_choices=(("amateur", "Любитель"),),
                gender_choices=(("male", "Мужчины"),),
                duration_choices=(("single", "Однодневный"),),
                club_choices=(("club-a", "Клуб А"),),
            ),
            [],
        )

    def test_collects_labels_in_form_order(self) -> None:
        chips = build_home_filter_chips(
            city="Химки",
            category="amateur",
            gender="male",
            duration="multi",
            club_filter="__platform__",
            category_choices=(("amateur", "Любитель"),),
            gender_choices=(("male", "Мужчины"),),
            duration_choices=(("multi", "Многодневный"),),
            club_choices=(("club-a", "Клуб А"),),
        )

        self.assertEqual(
            chips,
            ["Химки", "Любитель", "Мужчины", "Многодневный", "TennisFan"],
        )

    def test_named_club_uses_club_title(self) -> None:
        chips = build_home_filter_chips(
            city="",
            category="",
            gender="",
            duration="",
            club_filter="club-a",
            category_choices=(),
            gender_choices=(),
            duration_choices=(),
            club_choices=(("club-a", "Клуб А"),),
        )

        self.assertEqual(chips, ["Клуб А"])
