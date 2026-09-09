"""Юнит-тесты: сортировка матрицы результатов по местам."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.test import TestCase

from apps.tournaments.models import Match, TournamentStatus
from apps.tournaments.round_robin import (
    compute_standings,
    get_match_matrix,
    order_matrix_by_standings,
)
from tests.support.factories import make_player, make_tournament


@dataclass
class _FakeEntity:
    """Простая сущность с id для проверки перестановки."""

    id: int
    name: str = ""


class OrderMatrixByStandingsUnitTestCase(TestCase):
    """Проверка перестановки строк/столбцов матрицы."""

    def test_reorders_rows_and_columns_by_standings_place(self) -> None:
        """Первое место должно оказаться в начале матрицы, не по алфавиту."""
        a = _FakeEntity(id=1, name="Альфа")
        b = _FakeEntity(id=2, name="Бета")
        c = _FakeEntity(id=3, name="Гамма")
        participants = [a, b, c]
        # Ячейка (i, j) хранит метку «i→j», чтобы проверить перестановку осей.
        matrix = [
            [
                {"win": None, "label": "a→a"},
                {"win": 1, "label": "a→b"},
                {"win": 0, "label": "a→c"},
            ],
            [
                {"win": 0, "label": "b→a"},
                {"win": None, "label": "b→b"},
                {"win": 1, "label": "b→c"},
            ],
            [
                {"win": 1, "label": "c→a"},
                {"win": 0, "label": "c→b"},
                {"win": None, "label": "c→c"},
            ],
        ]
        # Места: Гамма=1, Альфа=2, Бета=3 (обратно алфавиту a,b,c).
        standings = [
            {"place": 1, "player": c, "team": None},
            {"place": 2, "player": a, "team": None},
            {"place": 3, "player": b, "team": None},
        ]

        ordered, new_matrix = order_matrix_by_standings(participants, matrix, standings)

        self.assertEqual([p.id for p in ordered], [3, 1, 2])
        self.assertEqual(
            [cell["label"] for cell in new_matrix[0]],
            ["c→c", "c→a", "c→b"],
        )
        self.assertEqual(new_matrix[1][0]["label"], "a→c")
        self.assertEqual(new_matrix[2][0]["label"], "b→c")

    def test_noop_when_already_sorted(self) -> None:
        """Если порядок совпадает со standings — матрица не меняется."""
        a = _FakeEntity(id=10)
        b = _FakeEntity(id=20)
        participants = [a, b]
        matrix = [
            [{"win": None}, {"win": 1}],
            [{"win": 0}, {"win": None}],
        ]
        standings = [
            {"place": 1, "player": a, "team": None},
            {"place": 2, "player": b, "team": None},
        ]

        ordered, new_matrix = order_matrix_by_standings(participants, matrix, standings)

        self.assertIs(ordered, participants)
        self.assertIs(new_matrix, matrix)


class OrderMatrixIntegrationTestCase(TestCase):
    """Интеграция: матрица кругового турнира следует порядку мест."""

    def test_matrix_first_row_is_first_place(self) -> None:
        """Игрок с 1-м местом — первая строка матрицы после перестановки."""
        # Фамилии специально: победитель по алфавиту последний.
        first = make_player(
            email_suffix="rr-mtx-first",
            last_name="Яковлев",
            first_name="Иван",
            points=3000.0,
        )
        second = make_player(
            email_suffix="rr-mtx-second",
            last_name="Абрамов",
            first_name="Пётр",
            points=2500.0,
        )
        tournament = make_tournament(
            name="Матрица по местам",
            slug="rr-matrix-by-place",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            bracket_generated=True,
            start_date=date.today(),
        )
        tournament.participants.add(first, second)
        Match.objects.create(
            tournament=tournament,
            round_name="Тур 1",
            round_index=1,
            round_order=1,
            player1=first,
            player2=second,
            winner=first,
            status=Match.MatchStatus.COMPLETED,
            player1_set1=6,
            player2_set1=4,
        )

        participants, matrix = get_match_matrix(tournament)
        standings = compute_standings(tournament)
        # До перестановки: алфавит — Абрамов первый.
        self.assertEqual(participants[0].id, second.id)

        ordered, _ = order_matrix_by_standings(participants, matrix, standings)

        self.assertEqual(ordered[0].id, first.id)
        self.assertEqual(standings[0]["player"].id, first.id)
        self.assertEqual(standings[0]["place"], 1)
