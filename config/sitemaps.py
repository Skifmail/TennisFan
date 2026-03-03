"""
Sitemap для поисковых систем (Google, Yandex).
Используется по адресу /sitemap.xml.
"""

from typing import cast

from django.contrib.sitemaps import Sitemap
from django.urls import reverse


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
