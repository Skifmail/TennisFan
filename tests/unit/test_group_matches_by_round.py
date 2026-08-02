"""Юнит-тесты группировки матчей по турам для сетки управления."""

from datetime import date, datetime

from django.test import TestCase
from django.utils import timezone

from apps.tournaments.models import Match, Tournament
from apps.tournaments.views import _group_matches_by_round
from apps.users.models import Player, User


class GroupMatchesByRoundTestCase(TestCase):
    """Группировка матчей возвращает дедлайн тура для отображения в шапке."""

    def setUp(self) -> None:
        self.tournament = Tournament.objects.create(
            name="Дедлайны туров",
            slug="round-deadlines-group",
            city="Москва",
            start_date=date(2026, 1, 1),
            format="fan",
        )
        self.players = [
            Player.objects.create(
                user=User.objects.create_user(
                    email=f"round-dl-{i}@test.local",
                    password="x",
                )
            )
            for i in range(4)
        ]

    def test_returns_deadline_per_round(self) -> None:
        deadline_r1 = timezone.make_aware(datetime(2026, 1, 8, 12, 0, 0))
        deadline_r2 = timezone.make_aware(datetime(2026, 1, 15, 12, 0, 0))
        Match.objects.create(
            tournament=self.tournament,
            round_name="Тур 1",
            round_index=1,
            round_order=1,
            player1=self.players[0],
            player2=self.players[1],
            status=Match.MatchStatus.SCHEDULED,
            deadline=deadline_r1,
        )
        Match.objects.create(
            tournament=self.tournament,
            round_name="Тур 1",
            round_index=1,
            round_order=2,
            player1=self.players[2],
            player2=self.players[3],
            status=Match.MatchStatus.SCHEDULED,
            deadline=deadline_r1,
        )
        Match.objects.create(
            tournament=self.tournament,
            round_name="Тур 2",
            round_index=2,
            round_order=1,
            player1=self.players[0],
            player2=self.players[2],
            status=Match.MatchStatus.SCHEDULED,
            deadline=deadline_r2,
        )

        rounds = _group_matches_by_round(
            list(self.tournament.matches.order_by("round_index", "round_order"))
        )

        self.assertEqual(len(rounds), 2)
        name1, matches1, dl1 = rounds[0]
        name2, matches2, dl2 = rounds[1]
        self.assertEqual(name1, "Тур 1")
        self.assertEqual(len(matches1), 2)
        self.assertEqual(dl1, deadline_r1)
        self.assertEqual(name2, "Тур 2")
        self.assertEqual(len(matches2), 1)
        self.assertEqual(dl2, deadline_r2)

    def test_deadline_none_when_matches_have_no_deadline(self) -> None:
        Match.objects.create(
            tournament=self.tournament,
            round_name="Тур 1",
            round_index=1,
            round_order=1,
            player1=self.players[0],
            player2=self.players[1],
            status=Match.MatchStatus.SCHEDULED,
            deadline=None,
        )

        rounds = _group_matches_by_round(list(self.tournament.matches.all()))

        self.assertEqual(len(rounds), 1)
        _, _, deadline = rounds[0]
        self.assertIsNone(deadline)
