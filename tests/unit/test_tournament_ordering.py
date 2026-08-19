"""Юнит-тесты: сортировка турниров на главной."""

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.tournaments.models import (
    Tournament,
    TournamentStatus,
)
from apps.tournaments.platform_home import (
    order_tournaments_active_first,
    order_with_cancelled_last,
)


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

    def test_cancelled_are_always_last(self) -> None:
        now = timezone.now()
        cancelled = Tournament.objects.create(
            name="Отменённый",
            slug="sort-cancelled",
            city="Москва",
            start_date=date.today() + timedelta(days=10),
            format="round_robin",
            status=TournamentStatus.CANCELLED,
        )
        upcoming = Tournament.objects.create(
            name="Набор",
            slug="sort-upcoming-keep",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.UPCOMING,
        )
        active = Tournament.objects.create(
            name="В игре",
            slug="sort-active-keep",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.ACTIVE,
        )
        Tournament.objects.filter(pk=cancelled.pk).update(created_at=now)
        Tournament.objects.filter(pk=upcoming.pk).update(
            created_at=now - timedelta(days=2)
        )
        Tournament.objects.filter(pk=active.pk).update(
            created_at=now - timedelta(days=3)
        )

        ordered = list(
            order_tournaments_active_first(
                Tournament.objects.filter(
                    slug__in=(
                        "sort-cancelled",
                        "sort-upcoming-keep",
                        "sort-active-keep",
                    )
                )
            ).values_list("slug", flat=True)
        )
        self.assertEqual(
            ordered,
            ["sort-active-keep", "sort-upcoming-keep", "sort-cancelled"],
        )


class OrderWithCancelledLastTestCase(TestCase):
    """Отменённые турниры в конце при сортировке по дате старта."""

    def test_cancelled_after_later_start_dates(self) -> None:
        cancelled = Tournament.objects.create(
            name="Отмена свежая",
            slug="date-cancelled",
            city="Москва",
            start_date=date.today() + timedelta(days=30),
            format="round_robin",
            status=TournamentStatus.CANCELLED,
        )
        older_active = Tournament.objects.create(
            name="Старый активный",
            slug="date-active-old",
            city="Москва",
            start_date=date.today() - timedelta(days=10),
            format="round_robin",
            status=TournamentStatus.ACTIVE,
        )
        newer_upcoming = Tournament.objects.create(
            name="Новый набор",
            slug="date-upcoming-new",
            city="Москва",
            start_date=date.today() + timedelta(days=5),
            format="round_robin",
            status=TournamentStatus.UPCOMING,
        )

        ordered = list(
            order_with_cancelled_last(
                Tournament.objects.filter(
                    slug__in=(cancelled.slug, older_active.slug, newer_upcoming.slug)
                ),
                "-start_date",
                "-pk",
            ).values_list("slug", flat=True)
        )
        self.assertEqual(
            ordered,
            ["date-upcoming-new", "date-active-old", "date-cancelled"],
        )
