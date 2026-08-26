"""Тесты sitemap и SEO каталога турниров."""

import json
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from apps.core.geo import GeoRegion
from apps.core.models import GeoArea
from apps.tournaments.landing import iter_sitemap_landings
from apps.tournaments.models import Tournament, TournamentStatus
from apps.tournaments.seo import build_tournament_sports_event_json


class SitemapSeoTestCase(TestCase):
    """Sitemap содержит турниры и фильтры; meta и JSON-LD на страницах."""

    def setUp(self) -> None:
        self.client = Client()
        self.area = GeoArea.objects.filter(
            region=GeoRegion.MOSCOW, is_active=True
        ).first()
        self.tournament = Tournament.objects.create(
            name="SEO турнир Юго-Восток",
            slug="seo-tournament",
            city="Москва",
            region=GeoRegion.MOSCOW,
            geo_area=self.area,
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.UPCOMING,
            gender="open",
            entry_fee=500,
            variant="singles",
        )

    def test_sitemap_lists_tournament_and_filters(self) -> None:
        response = self.client.get(reverse("sitemap"), secure=True)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("/tournaments/seo-tournament/", body)
        self.assertIn("/tournaments/moscow/", body)
        self.assertIn("/tournaments/moskovskaya-oblast/", body)
        if self.area:
            self.assertIn(f"/tournaments/moscow/{self.area.slug}/", body)

    def test_cancelled_tournament_is_excluded(self) -> None:
        self.tournament.status = TournamentStatus.CANCELLED
        self.tournament.save(update_fields=["status"])
        response = self.client.get(reverse("sitemap"), secure=True)
        self.assertNotIn("/tournaments/seo-tournament/", response.content.decode())

    def test_iter_sitemap_landings_covers_regions_and_areas(self) -> None:
        urls = {item.url for item in iter_sitemap_landings()}
        self.assertIn("/tournaments/moscow/", urls)
        self.assertIn("/tournaments/moscow/singles/", urls)
        self.assertIn("/tournaments/moscow/doubles/", urls)
        # Матрицу зона×формат в sitemap не кладём.
        if self.area:
            self.assertIn(f"/tournaments/moscow/{self.area.slug}/", urls)
            self.assertNotIn(f"/tournaments/moscow/{self.area.slug}/singles/", urls)

    def test_catalog_meta_description(self) -> None:
        response = self.client.get(reverse("tournament_list"), secure=True)
        self.assertContains(response, 'name="description"', html=False)
        self.assertContains(response, "Любительские турниры по теннису")

    def test_region_landing_meta_uses_heading(self) -> None:
        response = self.client.get(
            reverse("tournament_region_landing", kwargs={"region_slug": "moscow"}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "в Москве")

    def test_sports_event_json_ld(self) -> None:
        payload = build_tournament_sports_event_json(
            self.tournament,
            absolute_url="https://example.test/tournaments/seo-tournament/",
            site_base_url="https://example.test",
        )
        data = json.loads(payload)
        self.assertEqual(data["@type"], "SportsEvent")
        self.assertEqual(data["name"], self.tournament.name)
        self.assertEqual(data["sport"], "Tennis")

        response = self.client.get(
            reverse("tournament_detail", kwargs={"slug": self.tournament.slug}),
            secure=True,
        )
        self.assertContains(response, "application/ld+json")
        self.assertContains(response, "SportsEvent")
