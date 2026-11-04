"""Юнит-тесты ручного формирования пар в турнирах."""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.tournaments.manual_pairing import (
    ManualPairingError,
    ensure_round_robin_schedule,
    load_entities,
    match_entity_token,
    pair_key,
    save_manual_round,
    start_manual_tournament,
    update_manual_pair,
)
from apps.tournaments.models import Match, PairingMode, TournamentStatus
from apps.users.models import Player
from tests.support.factories import make_player, make_tournament
from tests.support.matches import complete_match


class ManualPairingTestCase(TestCase):
    """Проверяет запуск, составление пар и автоматическое продолжение турнира."""

    def setUp(self) -> None:
        self.notify_start = patch(
            "apps.telegram_bot.notifications.notify_tournament_start"
        ).start()
        patch("apps.telegram_bot.notifications.notify_match_created").start()
        self.addCleanup(patch.stopall)
        self.bye_player = Player.objects.filter(is_bye=True).first()

    def _make_tournament(
        self,
        count: int,
        *,
        fmt: str = "single_elimination",
        slug: str = "manual-cup",
    ):
        tournament = make_tournament(
            name="Ручной турнир",
            slug=slug,
            format=fmt,
            max_participants=max(16, count),
        )
        players = []
        for index in range(count):
            player = make_player(
                email_suffix=f"{slug}-{index}",
                points=2000 - index * 100,
            )
            player.ntrp_level = 5.0 - index * 0.25
            player.save(update_fields=["ntrp_level"])
            players.append(player)
        tournament.participants.set(players)
        return tournament, players

    def test_start_manual_tournament_closes_registration_and_notifies(self) -> None:
        tournament, _ = self._make_tournament(4)

        ok, message = start_manual_tournament(tournament)

        self.assertTrue(ok, message)
        tournament.refresh_from_db()
        self.assertEqual(tournament.pairing_mode, PairingMode.MANUAL)
        self.assertIsNotNone(tournament.manual_started_at)
        self.assertTrue(tournament.bracket_generated)
        self.assertEqual(tournament.status, TournamentStatus.ACTIVE)
        self.assertTrue(tournament.uses_manual_pairing())
        self.notify_start.assert_called_once()

    def test_entities_are_sorted_from_strongest_to_weakest(self) -> None:
        tournament, players = self._make_tournament(3)
        start_manual_tournament(tournament)

        entities = load_entities(tournament)

        self.assertEqual([entity.pk for entity in entities], [p.pk for p in players])
        self.assertGreaterEqual(entities[0].strength, entities[-1].strength)

    def test_elimination_rounds_are_advanced_automatically(self) -> None:
        tournament, _ = self._make_tournament(4)
        start_manual_tournament(tournament)
        entities = load_entities(tournament)

        ok, message = save_manual_round(
            tournament,
            pairs=[
                (entities[0], entities[3]),
                (entities[1], entities[2]),
            ],
            leftover=None,
            auto_next=True,
        )

        self.assertTrue(ok, message)
        first_round = list(
            tournament.matches.filter(round_index=1, is_consolation=False).order_by(
                "round_order"
            )
        )
        self.assertEqual(len(first_round), 2)

        complete_match(first_round[0])
        self.assertFalse(
            tournament.matches.filter(round_index=2, is_consolation=False).exists()
        )

        complete_match(first_round[1])
        second_round = list(
            tournament.matches.filter(round_index=2, is_consolation=False)
        )
        self.assertEqual(len(second_round), 1)

        complete_match(second_round[0])
        tournament.refresh_from_db()
        self.assertEqual(tournament.status, TournamentStatus.COMPLETED)

    def test_final_completes_even_when_auto_next_is_disabled(self) -> None:
        tournament, _ = self._make_tournament(2)
        start_manual_tournament(tournament)
        entities = load_entities(tournament)

        ok, message = save_manual_round(
            tournament,
            pairs=[(entities[0], entities[1])],
            leftover=None,
            auto_next=False,
        )
        self.assertTrue(ok, message)

        final = tournament.matches.get(round_index=1, is_consolation=False)
        complete_match(final)

        tournament.refresh_from_db()
        self.assertEqual(tournament.status, TournamentStatus.COMPLETED)

    def test_odd_elimination_creates_bye_and_reaches_final(self) -> None:
        self.assertIsNotNone(self.bye_player)
        tournament, _ = self._make_tournament(3)
        start_manual_tournament(tournament)
        entities = load_entities(tournament)

        ok, message = save_manual_round(
            tournament,
            pairs=[(entities[0], entities[1])],
            leftover=entities[2],
            auto_next=True,
        )

        self.assertTrue(ok, message)
        first_round = list(
            tournament.matches.filter(round_index=1, is_consolation=False).order_by(
                "round_order"
            )
        )
        self.assertEqual(len(first_round), 2)
        bye_match = next(match for match in first_round if match.is_bye_match())
        self.assertEqual(bye_match.status, Match.MatchStatus.WALKOVER)

        real_match = next(match for match in first_round if not match.is_bye_match())
        complete_match(real_match)
        final = tournament.matches.get(round_index=2, is_consolation=False)
        players = {final.player1_id, final.player2_id}
        self.assertIn(entities[2].pk, players)
        self.assertNotIn(self.bye_player.pk, players)

        bye_match.refresh_from_db()
        real_match.refresh_from_db()
        self.assertEqual(bye_match.next_match_id, final.pk)
        self.assertEqual(real_match.next_match_id, final.pk)

    def test_round_robin_auto_schedule_has_no_repeated_pairs(self) -> None:
        tournament, _ = self._make_tournament(5, fmt="round_robin")
        start_manual_tournament(tournament)
        entities = load_entities(tournament)

        ok, message = save_manual_round(
            tournament,
            pairs=[(entities[0], entities[4]), (entities[1], entities[3])],
            leftover=entities[2],
            auto_next=True,
        )

        self.assertTrue(ok, message)
        matches = list(tournament.matches.filter(is_consolation=False))
        self.assertEqual(len(matches), 10)
        self.assertEqual(
            sorted({match.round_index for match in matches}),
            [1, 2, 3, 4, 5],
        )
        keys = {
            pair_key(
                match_entity_token(match, 1) or "",
                match_entity_token(match, 2) or "",
            )
            for match in matches
        }
        self.assertEqual(len(keys), 10)

    def test_round_robin_even_schedule_has_all_pairs_once(self) -> None:
        tournament, _ = self._make_tournament(4, fmt="round_robin")
        start_manual_tournament(tournament)
        entities = load_entities(tournament)

        ok, message = save_manual_round(
            tournament,
            pairs=[(entities[0], entities[3]), (entities[1], entities[2])],
            leftover=None,
            auto_next=True,
        )
        self.assertTrue(ok, message)

        matches = list(tournament.matches.filter(is_consolation=False))
        self.assertEqual(len(matches), 6)
        self.assertEqual(
            sorted({match.round_index for match in matches}),
            [1, 2, 3],
        )
        keys = {
            pair_key(
                match_entity_token(match, 1) or "",
                match_entity_token(match, 2) or "",
            )
            for match in matches
        }
        self.assertEqual(len(keys), 6)

    def test_round_robin_schedule_is_built_after_enabling_auto_next(self) -> None:
        tournament, _ = self._make_tournament(5, fmt="round_robin")
        start_manual_tournament(tournament)
        entities = load_entities(tournament)

        ok, message = save_manual_round(
            tournament,
            pairs=[(entities[0], entities[4]), (entities[1], entities[3])],
            leftover=entities[2],
            auto_next=False,
        )
        self.assertTrue(ok, message)
        self.assertEqual(
            tournament.matches.filter(is_consolation=False).count(),
            2,
        )

        tournament.manual_pairing_auto_next_rounds = True
        tournament.save(update_fields=["manual_pairing_auto_next_rounds"])
        ok, message = ensure_round_robin_schedule(tournament)

        self.assertTrue(ok, message)
        matches = list(tournament.matches.filter(is_consolation=False))
        self.assertEqual(len(matches), 10)
        self.assertEqual(
            sorted({match.round_index for match in matches}),
            [1, 2, 3, 4, 5],
        )
        keys = {
            pair_key(
                match_entity_token(match, 1) or "",
                match_entity_token(match, 2) or "",
            )
            for match in matches
        }
        self.assertEqual(len(keys), 10)

        ok, message = ensure_round_robin_schedule(tournament)
        self.assertFalse(ok)
        self.assertIn("уже созданы", message)

    def test_round_robin_manual_round_with_auto_next_completes_schedule(self) -> None:
        tournament, _ = self._make_tournament(6, fmt="round_robin")
        start_manual_tournament(tournament)
        entities = load_entities(tournament)

        ok, message = save_manual_round(
            tournament,
            pairs=[
                (entities[0], entities[5]),
                (entities[1], entities[4]),
                (entities[2], entities[3]),
            ],
            leftover=None,
            auto_next=False,
        )
        self.assertTrue(ok, message)
        for match in tournament.matches.filter(round_index=1):
            complete_match(match)

        ok, message = save_manual_round(
            tournament,
            pairs=[
                (entities[0], entities[4]),
                (entities[1], entities[3]),
                (entities[2], entities[5]),
            ],
            leftover=None,
            auto_next=True,
        )

        self.assertTrue(ok, message)
        matches = list(tournament.matches.filter(is_consolation=False))
        self.assertEqual(len(matches), 15)
        self.assertEqual(
            sorted({match.round_index for match in matches}),
            [1, 2, 3, 4, 5],
        )
        keys = {
            pair_key(
                match_entity_token(match, 1) or "",
                match_entity_token(match, 2) or "",
            )
            for match in matches
        }
        self.assertEqual(len(keys), 15)

    def test_ensure_schedule_after_several_manual_rounds(self) -> None:
        tournament, _ = self._make_tournament(5, fmt="round_robin")
        start_manual_tournament(tournament)
        entities = load_entities(tournament)

        manual_rounds = [
            (
                [(entities[0], entities[4]), (entities[1], entities[3])],
                entities[2],
            ),
            (
                [(entities[0], entities[3]), (entities[1], entities[2])],
                entities[4],
            ),
        ]
        for pairs, leftover in manual_rounds:
            ok, message = save_manual_round(
                tournament,
                pairs=pairs,
                leftover=leftover,
                auto_next=False,
            )
            self.assertTrue(ok, message)
            round_index = tournament.matches.order_by("-round_index").first()
            for match in tournament.matches.filter(round_index=round_index.round_index):
                complete_match(match)

        tournament.manual_pairing_auto_next_rounds = True
        tournament.save(update_fields=["manual_pairing_auto_next_rounds"])
        ok, message = ensure_round_robin_schedule(tournament)

        self.assertTrue(ok, message)
        matches = list(tournament.matches.filter(is_consolation=False))
        self.assertEqual(len(matches), 10)
        self.assertEqual(
            sorted({match.round_index for match in matches}),
            [1, 2, 3, 4, 5],
        )
        keys = {
            pair_key(
                match_entity_token(match, 1) or "",
                match_entity_token(match, 2) or "",
            )
            for match in matches
        }
        self.assertEqual(len(keys), 10)

    def test_round_robin_auto_schedule_for_eight_players(self) -> None:
        tournament, _ = self._make_tournament(8, fmt="round_robin")
        start_manual_tournament(tournament)
        entities = load_entities(tournament)

        ok, message = save_manual_round(
            tournament,
            pairs=[
                (entities[0], entities[7]),
                (entities[1], entities[6]),
                (entities[2], entities[5]),
                (entities[3], entities[4]),
            ],
            leftover=None,
            auto_next=True,
        )

        self.assertTrue(ok, message)
        matches = list(tournament.matches.filter(is_consolation=False))
        self.assertEqual(len(matches), 28)
        self.assertEqual(
            sorted({match.round_index for match in matches}),
            [1, 2, 3, 4, 5, 6, 7],
        )
        keys = {
            pair_key(
                match_entity_token(match, 1) or "",
                match_entity_token(match, 2) or "",
            )
            for match in matches
        }
        self.assertEqual(len(keys), 28)

    def test_round_robin_repeat_pair_requires_confirmation(self) -> None:
        tournament, _ = self._make_tournament(4, fmt="round_robin")
        start_manual_tournament(tournament)
        entities = load_entities(tournament)
        pairs = [(entities[0], entities[3]), (entities[1], entities[2])]

        ok, message = save_manual_round(
            tournament,
            pairs=pairs,
            leftover=None,
            auto_next=False,
        )
        self.assertTrue(ok, message)
        for match in tournament.matches.filter(round_index=1):
            complete_match(match)

        with self.assertRaises(ManualPairingError) as error:
            save_manual_round(
                tournament,
                pairs=pairs,
                leftover=None,
                auto_next=False,
            )
        self.assertIn("повторные", str(error.exception).lower())

        ok, message = save_manual_round(
            tournament,
            pairs=pairs,
            leftover=None,
            auto_next=False,
            allow_rematch=True,
        )
        self.assertTrue(ok, message)
        self.assertTrue(
            tournament.matches.filter(round_index=2, is_consolation=False).exists()
        )

    def test_update_pair_swaps_player_from_another_match(self) -> None:
        tournament, _ = self._make_tournament(4)
        start_manual_tournament(tournament)
        entities = load_entities(tournament)
        save_manual_round(
            tournament,
            pairs=[
                (entities[0], entities[3]),
                (entities[1], entities[2]),
            ],
            leftover=None,
            auto_next=False,
        )
        matches = list(tournament.matches.filter(round_index=1).order_by("round_order"))
        first, second = matches

        ok, message = update_manual_pair(
            tournament,
            match=first,
            entity_a=entities[1],
            entity_b=entities[3],
        )

        self.assertTrue(ok, message)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(
            {first.player1_id, first.player2_id}, {entities[1].pk, entities[3].pk}
        )
        self.assertEqual(
            {second.player1_id, second.player2_id}, {entities[0].pk, entities[2].pk}
        )

    def test_update_completed_pair_is_rejected(self) -> None:
        tournament, _ = self._make_tournament(2)
        start_manual_tournament(tournament)
        entities = load_entities(tournament)
        save_manual_round(
            tournament,
            pairs=[(entities[0], entities[1])],
            leftover=None,
            auto_next=False,
        )
        match = tournament.matches.get(round_index=1)
        complete_match(match)
        match.refresh_from_db()

        ok, message = update_manual_pair(
            tournament,
            match=match,
            entity_a=entities[0],
            entity_b=entities[1],
        )

        self.assertFalse(ok)
        self.assertIn("сыгран", message.lower())

    def test_manual_started_flag_survives_empty_bracket(self) -> None:
        tournament, _ = self._make_tournament(2)
        start_manual_tournament(tournament)

        self.assertTrue(tournament.manual_pairing_has_started())
        self.assertIsNotNone(tournament.manual_started_at)
        self.assertFalse(tournament.matches.exists())
        self.assertLessEqual(tournament.manual_started_at, timezone.now())
