"""
Sitemap для поисковых систем (Google, Yandex).
Используется по адресу /sitemap.xml.
"""

from __future__ import annotations

from typing import Any, cast

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from apps.tournaments.landing import TournamentLanding, iter_sitemap_landings
from apps.tournaments.models import Tournament, TournamentStatus


class StaticViewSitemap(Sitemap):
    """Статические страницы для sitemap.xml."""

    changefreq = "weekly"
    priority = 0.8

    def items(self) -> list[str]:
        return [
            "home",
            "rating",
            "hall_of_fame",
            "results",
            "rules",
            "pricing",
            "donate",
            "about_us",
            "contacts",
            "court_list",
            "tournament_list",
            "news_list",
            "training_list",
            "coach_list",
            "legal_index",
            "shop_list",
        ]

    def location(self, item: str) -> str:
        return cast(str, reverse(item))


class TournamentSitemap(Sitemap):
    """Публичные страницы турниров (без отменённых)."""

    changefreq = "daily"
    priority = 0.7

    def items(self):
        return (
            Tournament.objects.exclude(status=TournamentStatus.CANCELLED)
            .order_by("-updated_at")
            .only("slug", "updated_at")
        )

    def lastmod(self, obj: Tournament):
        return obj.updated_at

    def location(self, obj: Tournament) -> str:
        return cast(str, reverse("tournament_detail", kwargs={"slug": obj.slug}))


class TournamentFilterSitemap(Sitemap):
    """Фильтры каталога: регион, регион+формат, регион+зона/город."""

    changefreq = "daily"
    priority = 0.6

    def items(self) -> list[TournamentLanding]:
        return iter_sitemap_landings()

    def location(self, obj: TournamentLanding) -> str:
        return obj.url


#: Реестр sitemap для urls.py.
SITEMAPS: dict[str, Any] = {
    "static": StaticViewSitemap,
    "tournaments": TournamentSitemap,
    "tournament_filters": TournamentFilterSitemap,
}
