"""Юнит-тесты: сортировка турниров на главной."""

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.tournaments.models import (
    Tournament,
    TournamentStatus,
)
from apps.tournaments.platform_home import order_tournaments_active_first


class OrderTournamentsActiveFirstTestCase(TestCase):
    """Сортировка списков турниров на главной и на /tournaments/."""

    def test_in_game_first_then_newest_created(self) -> None:
        now = timezone.now()
        active = Tournament.objects.create(
            name="В игре",
            slug="sort-active",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.ACTIVE,
        )
        older_upcoming = Tournament.objects.create(
            name="Старый набор",
            slug="sort-upcoming-old",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.UPCOMING,
        )
        newer_upcoming = Tournament.objects.create(
            name="Новый набор",
            slug="sort-upcoming-new",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.UPCOMING,
        )
        Tournament.objects.filter(pk=older_upcoming.pk).update(
            created_at=now - timedelta(days=2)
        )
        Tournament.objects.filter(pk=newer_upcoming.pk).update(
            created_at=now - timedelta(days=1)
        )
        Tournament.objects.filter(pk=active.pk).update(
            created_at=now - timedelta(days=3)
        )

        ordered = list(
            order_tournaments_active_first(
                Tournament.objects.filter(slug__startswith="sort-")
            ).values_list("slug", flat=True)
        )
        self.assertEqual(
            ordered,
            ["sort-active", "sort-upcoming-new", "sort-upcoming-old"],
        )
