"""Классический посев олимпийской сетки: сильнейшие встречаются как можно позже."""

from __future__ import annotations

from datetime import date

from django.test import SimpleTestCase, TestCase

from apps.tournaments.fan import generate_bracket, seed_pairs
from apps.tournaments.models import Tournament
from apps.users.models import Player, User


class SeedPairsTestCase(SimpleTestCase):
    """Порядок пар первого круга по номерам посева."""

    def test_sixteen_matches_tennis_book_layout(self) -> None:
        self.assertEqual(
            seed_pairs(16),
            [
                (1, 16),
                (8, 9),
                (5, 12),
                (4, 13),
                (3, 14),
                (6, 11),
                (7, 10),
                (2, 15),
            ],
        )

    def test_eight_and_small_sizes(self) -> None:
        self.assertEqual(seed_pairs(8), [(1, 8), (4, 5), (3, 6), (2, 7)])
        self.assertEqual(seed_pairs(4), [(1, 4), (2, 3)])
        self.assertEqual(seed_pairs(2), [(1, 2)])

    def test_every_seed_once_and_top_seeds_split(self) -> None:
        for size in (2, 4, 8, 16, 32, 64):
            pairs = seed_pairs(size)
            seeds = [seed for pair in pairs for seed in pair]
            self.assertEqual(sorted(seeds), list(range(1, size + 1)))
            self.assertTrue(all(top + bottom == size + 1 for top, bottom in pairs))
            half = len(seeds) // 2
            self.assertIn(1, seeds[:half])
            self.assertIn(2, seeds[half:])


class GeneratedSeedingTestCase(TestCase):
    """Жеребьёвка ставит игроков по рейтингу в позиции посева."""

    def _tournament(self, slug: str, count: int, maximum: int) -> list[Player]:
        """Турнир с участниками по убыванию рейтинга; вернуть игроков по посеву."""
        players = [
            Player.objects.create(
                user=User.objects.create_user(
                    email=f"{slug}-{index}@test.local", password="x"
                ),
                total_points=1000 - index * 10,
            )
            for index in range(count)
        ]
        self.tournament = Tournament.objects.create(
            name=f"Посев {slug}",
            slug=slug,
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            bracket_generated=False,
            max_participants=maximum,
        )
        self.tournament.participants.set(players)
        ok, message = generate_bracket(self.tournament)
        self.assertTrue(ok, message)
        return players

    def test_first_and_second_seeds_are_in_different_halves(self) -> None:
        players = self._tournament("seed-eight", 8, 8)
        first_round = list(
            self.tournament.matches.filter(
                round_index=1, is_consolation=False
            ).order_by("round_order")
        )
        by_seed = {player.pk: index + 1 for index, player in enumerate(players)}
        pairs = [
            (by_seed[match.player1_id], by_seed[match.player2_id])
            for match in first_round
        ]
        self.assertEqual(pairs, [(1, 8), (4, 5), (3, 6), (2, 7)])

    def test_byes_go_to_top_seeds_as_second_side(self) -> None:
        players = self._tournament("seed-six", 6, 8)
        first_round = list(
            self.tournament.matches.filter(
                round_index=1, is_consolation=False
            ).order_by("round_order")
        )
        byes = [match for match in first_round if match.player2.is_bye]
        self.assertEqual(
            sorted(match.player1_id for match in byes),
            sorted([players[0].pk, players[1].pk]),
        )
        self.assertFalse(any(match.player1.is_bye for match in first_round))
