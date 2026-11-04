"""Интеграционные тесты страницы ручного формирования пар."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.tournaments.manual_pairing import load_entities
from apps.tournaments.models import Match, PairingMode, TournamentPostpaymentInvoice
from tests.support.factories import make_player, make_tournament, make_user
from tests.support.matches import complete_match


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
class ManualPairingViewsTestCase(TestCase):
    """Проверяет запуск турнира, страницу пар и возврат после результата."""

    def setUp(self) -> None:
        patch("apps.telegram_bot.notifications.notify_tournament_start").start()
        patch("apps.telegram_bot.notifications.notify_match_created").start()
        patch(
            "apps.tournaments.proposal_service.notify_participants_match_result_confirmed"
        ).start()
        self.addCleanup(patch.stopall)

        self.user = make_user(
            email="manager@test.local",
            is_staff=True,
            first_name="Менеджер",
            last_name="Турниров",
        )
        self.client.force_login(self.user)
        self.tournament = make_tournament(
            name="Ручной турнир",
            slug="manual-view-cup",
            format="single_elimination",
            allow_postpayment=False,
            max_participants=8,
        )
        self.players = [
            make_player(
                email_suffix=f"view-{index}",
                points=2000 - index * 100,
            )
            for index in range(4)
        ]
        for index, player in enumerate(self.players):
            player.ntrp_level = 5.0 - index * 0.5
            player.save(update_fields=["ntrp_level"])
        self.tournament.participants.set(self.players)

    def test_manage_page_offers_manual_start(self) -> None:
        response = self.client.get(
            reverse("tournament_manage", args=[self.tournament.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Как запустить турнир?")
        self.assertContains(response, 'value="manual"')

    def test_start_manual_redirects_to_pairs_and_renders_page(self) -> None:
        response = self.client.post(
            reverse("tournament_manage_generate_bracket", args=[self.tournament.slug]),
            {"pairing_mode": "manual"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("tournament_manage_pairs", args=[self.tournament.slug]),
        )
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.pairing_mode, PairingMode.MANUAL)
        self.assertTrue(self.tournament.manual_pairing_has_started())

        page = self.client.get(
            reverse("tournament_manage_pairs", args=[self.tournament.slug])
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Формирование пар")
        self.assertContains(page, self.players[0].get_display_name())
        self.assertContains(page, "5.00")

    def test_save_pairs_creates_matches_and_result_returns_to_pairs(self) -> None:
        self.client.post(
            reverse("tournament_manage_generate_bracket", args=[self.tournament.slug]),
            {"pairing_mode": "manual"},
        )
        entities = load_entities(self.tournament)
        pairs_url = reverse("tournament_manage_pairs", args=[self.tournament.slug])

        response = self.client.post(
            reverse("tournament_manage_pairs_save", args=[self.tournament.slug]),
            {
                "pair_1_a": entities[0].token,
                "pair_1_b": entities[3].token,
                "pair_2_a": entities[1].token,
                "pair_2_b": entities[2].token,
                "auto_next": "1",
            },
        )

        self.assertRedirects(response, pairs_url)
        matches = list(
            self.tournament.matches.filter(round_index=1).order_by("round_order")
        )
        self.assertEqual(len(matches), 2)

        match = matches[0]
        result_response = self.client.post(
            reverse(
                "tournament_manage_match_result",
                args=[self.tournament.slug, match.pk],
            ),
            {
                "winner_id": match.player1_id,
                "score1": "6",
                "score2": "3",
                "set2_p1": "6",
                "set2_p2": "4",
                "next": pairs_url,
            },
        )

        self.assertEqual(result_response.status_code, 302)
        self.assertEqual(result_response.url, pairs_url)
        match.refresh_from_db()
        self.assertEqual(match.status, Match.MatchStatus.COMPLETED)

    def test_pairs_page_offers_leftover_select_for_odd_count(self) -> None:
        self.tournament.participants.set(self.players[:3])
        self.client.post(
            reverse("tournament_manage_generate_bracket", args=[self.tournament.slug]),
            {"pairing_mode": "manual"},
        )

        page = self.client.get(
            reverse("tournament_manage_pairs", args=[self.tournament.slug])
        )

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="pairs-leftover-select"')
        self.assertContains(page, 'name="leftover_token"')

    def test_update_pair_view_swaps_players(self) -> None:
        self.client.post(
            reverse("tournament_manage_generate_bracket", args=[self.tournament.slug]),
            {"pairing_mode": "manual"},
        )
        entities = load_entities(self.tournament)
        self.client.post(
            reverse("tournament_manage_pairs_save", args=[self.tournament.slug]),
            {
                "pair_1_a": entities[0].token,
                "pair_1_b": entities[3].token,
                "pair_2_a": entities[1].token,
                "pair_2_b": entities[2].token,
                "auto_next": "0",
            },
        )
        matches = list(
            self.tournament.matches.filter(round_index=1).order_by("round_order")
        )
        first, second = matches

        response = self.client.post(
            reverse(
                "tournament_manage_pair_update",
                args=[self.tournament.slug, first.pk],
            ),
            {
                "token_a": entities[1].token,
                "token_b": entities[3].token,
                "next": reverse("tournament_manage_pairs", args=[self.tournament.slug]),
            },
        )

        self.assertRedirects(
            response,
            reverse("tournament_manage_pairs", args=[self.tournament.slug]),
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(
            {first.player1_id, first.player2_id},
            {entities[1].pk, entities[3].pk},
        )
        self.assertEqual(
            {second.player1_id, second.player2_id},
            {entities[0].pk, entities[2].pk},
        )

    def test_start_manual_is_blocked_while_postpayment_is_pending(self) -> None:
        self.tournament.allow_postpayment = True
        self.tournament.postpayment_window_started_at = timezone.now()
        self.tournament.save(
            update_fields=["allow_postpayment", "postpayment_window_started_at"]
        )
        TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.players[0].user,
            amount=1000,
            due_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.post(
            reverse(
                "tournament_manage_pairs_start",
                args=[self.tournament.slug],
            )
        )

        self.assertRedirects(
            response,
            reverse("tournament_manage", args=[self.tournament.slug]),
        )
        self.tournament.refresh_from_db()
        self.assertFalse(self.tournament.manual_pairing_has_started())
        self.assertFalse(self.tournament.matches.filter(is_consolation=False).exists())

    def test_manual_start_after_postpayment_does_not_auto_generate(self) -> None:
        self.tournament.allow_postpayment = True
        self.tournament.postpayment_window_started_at = timezone.now()
        self.tournament.save(
            update_fields=["allow_postpayment", "postpayment_window_started_at"]
        )
        TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.players[0].user,
            amount=1000,
            due_at=timezone.now() + timedelta(hours=1),
            status=TournamentPostpaymentInvoice.Status.PAID,
            paid_at=timezone.now(),
        )

        response = self.client.post(
            reverse(
                "tournament_manage_pairs_start",
                args=[self.tournament.slug],
            )
        )

        self.assertRedirects(
            response,
            reverse("tournament_manage_pairs", args=[self.tournament.slug]),
        )
        self.tournament.refresh_from_db()
        self.assertTrue(self.tournament.manual_pairing_has_started())
        self.assertTrue(self.tournament.uses_manual_pairing())
        self.assertFalse(self.tournament.matches.filter(is_consolation=False).exists())

    def test_toggle_auto_next_builds_round_robin_schedule(self) -> None:
        tournament = make_tournament(
            name="Круговой турнир",
            slug="round-robin-view-cup",
            format="round_robin",
            allow_postpayment=False,
            max_participants=8,
        )
        tournament.participants.set(self.players)
        self.client.post(
            reverse("tournament_manage_generate_bracket", args=[tournament.slug]),
            {"pairing_mode": "manual"},
        )
        entities = load_entities(tournament)
        self.client.post(
            reverse("tournament_manage_pairs_save", args=[tournament.slug]),
            {
                "pair_1_a": entities[0].token,
                "pair_1_b": entities[3].token,
                "pair_2_a": entities[1].token,
                "pair_2_b": entities[2].token,
                "auto_next": "0",
            },
        )
        self.assertEqual(
            tournament.matches.filter(is_consolation=False).count(),
            2,
        )

        response = self.client.post(
            reverse("tournament_manage_pairs_settings", args=[tournament.slug]),
            {"auto_next": "1"},
        )

        self.assertRedirects(
            response,
            reverse("tournament_manage_pairs", args=[tournament.slug]),
        )
        tournament.refresh_from_db()
        self.assertTrue(tournament.manual_pairing_auto_next_rounds)
        self.assertEqual(
            tournament.matches.filter(is_consolation=False).count(),
            6,
        )
        self.assertEqual(
            sorted(
                {
                    match.round_index
                    for match in tournament.matches.filter(is_consolation=False)
                }
            ),
            [1, 2, 3],
        )

    def test_toggle_auto_next_advances_completed_elimination_round(self) -> None:
        self.client.post(
            reverse("tournament_manage_generate_bracket", args=[self.tournament.slug]),
            {"pairing_mode": "manual"},
        )
        entities = load_entities(self.tournament)
        self.client.post(
            reverse("tournament_manage_pairs_save", args=[self.tournament.slug]),
            {
                "pair_1_a": entities[0].token,
                "pair_1_b": entities[3].token,
                "pair_2_a": entities[1].token,
                "pair_2_b": entities[2].token,
                "auto_next": "0",
            },
        )
        for match in self.tournament.matches.filter(
            round_index=1, is_consolation=False
        ):
            complete_match(match)
        self.assertFalse(
            self.tournament.matches.filter(round_index=2, is_consolation=False).exists()
        )

        response = self.client.post(
            reverse(
                "tournament_manage_pairs_settings",
                args=[self.tournament.slug],
            ),
            {"auto_next": "1"},
        )

        self.assertRedirects(
            response,
            reverse("tournament_manage_pairs", args=[self.tournament.slug]),
        )
        self.assertTrue(
            self.tournament.matches.filter(round_index=2, is_consolation=False).exists()
        )

    def test_manual_pairs_page_rejects_unsupported_format(self) -> None:
        olympic = make_tournament(
            name="Олимпийский за все места",
            slug="olympic-view-cup",
            format="olympic_consolation",
        )
        olympic.participants.set(self.players)
        response = self.client.get(
            reverse("tournament_manage_pairs", args=[olympic.slug])
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("tournament_manage", args=[olympic.slug]),
        )
