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

    def test_completed_before_cancelled_newer_finished_higher(self) -> None:
        now = timezone.now()
        today = date.today()
        cancelled = Tournament.objects.create(
            name="Отменённый",
            slug="sort-cancelled-after-done",
            city="Москва",
            start_date=today + timedelta(days=20),
            end_date=today + timedelta(days=21),
            format="round_robin",
            status=TournamentStatus.CANCELLED,
        )
        older_completed = Tournament.objects.create(
            name="Завершён давно",
            slug="sort-completed-old",
            city="Москва",
            start_date=today - timedelta(days=30),
            end_date=today - timedelta(days=20),
            format="round_robin",
            status=TournamentStatus.COMPLETED,
        )
        newer_completed = Tournament.objects.create(
            name="Завершён недавно",
            slug="sort-completed-new",
            city="Москва",
            start_date=today - timedelta(days=10),
            end_date=today - timedelta(days=2),
            format="round_robin",
            status=TournamentStatus.COMPLETED,
        )
        upcoming = Tournament.objects.create(
            name="Набор",
            slug="sort-upcoming-before-done",
            city="Москва",
            start_date=today,
            format="round_robin",
            status=TournamentStatus.UPCOMING,
        )
        Tournament.objects.filter(pk=cancelled.pk).update(created_at=now)
        Tournament.objects.filter(pk=newer_completed.pk).update(
            created_at=now - timedelta(days=40)
        )
        Tournament.objects.filter(pk=older_completed.pk).update(
            created_at=now - timedelta(days=1)
        )
        Tournament.objects.filter(pk=upcoming.pk).update(
            created_at=now - timedelta(days=3)
        )

        ordered = list(
            order_tournaments_active_first(
                Tournament.objects.filter(
                    slug__in=(
                        cancelled.slug,
                        older_completed.slug,
                        newer_completed.slug,
                        upcoming.slug,
                    )
                )
            ).values_list("slug", flat=True)
        )
        self.assertEqual(
            ordered,
            [
                "sort-upcoming-before-done",
                "sort-completed-new",
                "sort-completed-old",
                "sort-cancelled-after-done",
            ],
        )

    def test_completed_without_end_date_uses_start_date(self) -> None:
        today = date.today()
        now = timezone.now()
        without_end = Tournament.objects.create(
            name="Без даты окончания",
            slug="sort-completed-no-end",
            city="Москва",
            start_date=today - timedelta(days=3),
            format="round_robin",
            status=TournamentStatus.COMPLETED,
        )
        with_end = Tournament.objects.create(
            name="С датой окончания",
            slug="sort-completed-with-end",
            city="Москва",
            start_date=today - timedelta(days=30),
            end_date=today - timedelta(days=1),
            format="round_robin",
            status=TournamentStatus.COMPLETED,
        )
        Tournament.objects.filter(pk=without_end.pk).update(created_at=now)
        Tournament.objects.filter(pk=with_end.pk).update(
            created_at=now - timedelta(days=10)
        )

        ordered = list(
            order_tournaments_active_first(
                Tournament.objects.filter(slug__in=(without_end.slug, with_end.slug))
            ).values_list("slug", flat=True)
        )
        self.assertEqual(
            ordered,
            ["sort-completed-with-end", "sort-completed-no-end"],
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
