"""Интеграционные тесты: снятие участника кругового турнира после старта.

Вызывается pytest/Django test runner (tests/integration/).
Покрывает API withdraw_participant из apps/tournaments/withdraw.py.
Аналогов нет: test_tournament_cancel_restore покрывает только полную отмену.
Инструкция: Implement the plan as specified... complete all to-dos.
"""

from datetime import date, timedelta

from django.db.models import Q
from django.test import TestCase
from django.utils import timezone

from apps.subscriptions.fancoin import TOURNAMENT_REGISTRATION_COST
from apps.subscriptions.models import (
    FancoinTransaction,
    SubscriptionTier,
    UserSubscription,
)
from apps.tournaments.models import (
    Match,
    Tournament,
    TournamentPlayerResult,
    TournamentStatus,
    TournamentWithdrawal,
)
from apps.tournaments.postpayment import (
    _SUBSCRIPTION_SLOT_COVERAGE,
    mark_registration_covered,
)
from apps.tournaments.round_robin import (
    check_and_finalize_if_complete,
    generate_bracket,
)
from apps.tournaments.withdraw import (
    _truncate_notification_message,
    withdraw_participant,
)
from apps.users.models import Notification, Player, SkillLevel, User


def _matches_for_player(player: Player):
    """Фильтр матчей с участием игрока."""
    return Q(player1=player) | Q(player2=player)


class RoundRobinWithdrawTestCase(TestCase):
    """Снятие участника: walkover, условный FT, идемпотентность."""

    def setUp(self) -> None:
        self.admin = User.objects.create_user(email="admin@test.local", password="x")
        self.players: list[Player] = []
        self.subs: list[UserSubscription] = []
        tier = SubscriptionTier.objects.create(
            name="tier-withdraw-test",
            display_name="Test",
            fancoin_per_purchase=15,
            duration_days=30,
            is_visible=True,
            is_unlimited=False,
        )
        for i in range(4):
            user = User.objects.create_user(
                email=f"rr-w{i}@test.local",
                password="x",
                first_name=f"P{i}",
                last_name=f"L{i}",
            )
            player = Player.objects.create(
                user=user,
                skill_level=SkillLevel.AMATEUR,
                total_points=1000 + i * 10,
                hidden_rating=1000 + i * 10,
            )
            sub = UserSubscription.objects.create(
                user=user,
                tier=tier,
                start_date=timezone.now(),
                end_date=timezone.now() + timedelta(days=30),
                fancoin_balance=0,
                is_active=True,
            )
            self.players.append(player)
            self.subs.append(sub)

        self.tournament = Tournament.objects.create(
            name="RR withdraw test",
            slug="rr-withdraw-test",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
            entry_fee=0,
            allow_postpayment=False,
            is_one_day=False,
            max_participants=4,
            min_participants=2,
            status=TournamentStatus.UPCOMING,
            match_days_per_round=7,
        )
        self.tournament.allowed_categories.create(category=SkillLevel.AMATEUR)
        for player, sub in zip(self.players, self.subs, strict=True):
            self.tournament.participants.add(player)
            mark_registration_covered(
                self.tournament,
                player.user,
                _SUBSCRIPTION_SLOT_COVERAGE,
            )
            sub.fancoin_balance = 0
            sub.save(update_fields=["fancoin_balance"])

        ok, msg = generate_bracket(self.tournament)
        self.assertTrue(ok, msg)
        self.tournament.refresh_from_db()
        self.assertTrue(self.tournament.bracket_generated)

    def test_withdraw_without_played_matches_refunds_ft(self) -> None:
        """Без сыгранных матчей: walkover всех матчей игрока + возврат 3 FT."""
        leaver = self.players[0]
        scheduled_before = (
            Match.objects.filter(
                tournament=self.tournament,
                status=Match.MatchStatus.SCHEDULED,
            )
            .filter(_matches_for_player(leaver))
            .count()
        )
        self.assertGreater(scheduled_before, 0)

        ok, msg = withdraw_participant(
            self.tournament, player=leaver, withdrawn_by=self.admin
        )
        self.assertTrue(ok, msg)

        self.assertTrue(
            TournamentWithdrawal.objects.filter(
                tournament=self.tournament, player=leaver
            ).exists()
        )
        self.assertEqual(
            Match.objects.filter(
                tournament=self.tournament,
                status=Match.MatchStatus.SCHEDULED,
            )
            .filter(_matches_for_player(leaver))
            .count(),
            0,
        )
        self.assertEqual(
            Match.objects.filter(
                tournament=self.tournament,
                status=Match.MatchStatus.WALKOVER,
            )
            .filter(_matches_for_player(leaver))
            .count(),
            scheduled_before,
        )
        for m in Match.objects.filter(
            tournament=self.tournament,
            status=Match.MatchStatus.WALKOVER,
        ).filter(_matches_for_player(leaver)):
            self.assertNotEqual(m.winner_id, leaver.pk)

        self.subs[0].refresh_from_db()
        self.assertEqual(self.subs[0].fancoin_balance, TOURNAMENT_REGISTRATION_COST)
        self.assertFalse(
            self.tournament.registration_coverages.filter(user=leaver.user).exists()
        )
        self.assertTrue(
            FancoinTransaction.objects.filter(
                user=leaver.user,
                tournament=self.tournament,
                reason=FancoinTransaction.Reason.TOURNAMENT_WITHDRAWAL,
                direction=FancoinTransaction.Direction.REFUND,
            ).exists()
        )
        withdrawal = TournamentWithdrawal.objects.get(
            tournament=self.tournament, player=leaver
        )
        self.assertTrue(withdrawal.fancoin_refunded)
        self.assertTrue(self.tournament.participants.filter(pk=leaver.pk).exists())

    def test_withdraw_after_played_match_no_ft_refund(self) -> None:
        """После ≥1 сыгранного матча FT не возвращаются; чужие результаты целы."""
        leaver = self.players[0]
        other_a, other_b = self.players[1], self.players[2]

        leaver_match = (
            Match.objects.filter(tournament=self.tournament)
            .filter(_matches_for_player(leaver))
            .filter(status=Match.MatchStatus.SCHEDULED)
            .first()
        )
        self.assertIsNotNone(leaver_match)
        assert leaver_match is not None
        opponent = (
            leaver_match.player2
            if leaver_match.player1_id == leaver.pk
            else leaver_match.player1
        )
        leaver_match.winner = opponent
        leaver_match.player1_set1 = 6
        leaver_match.player2_set1 = 4
        leaver_match.player1_set2 = 6
        leaver_match.player2_set2 = 3
        leaver_match.status = Match.MatchStatus.COMPLETED
        leaver_match.completed_datetime = timezone.now()
        leaver_match.save()

        other_match = (
            Match.objects.filter(tournament=self.tournament)
            .filter(_matches_for_player(other_a))
            .filter(_matches_for_player(other_b))
            .filter(status=Match.MatchStatus.SCHEDULED)
            .first()
        )
        other_completed_pk = None
        other_winner_id = None
        other_status = None
        if other_match is not None:
            other_match.winner = other_a
            other_match.player1_set1 = 6
            other_match.player2_set1 = 1
            other_match.player1_set2 = 6
            other_match.player2_set2 = 2
            other_match.status = Match.MatchStatus.COMPLETED
            other_match.completed_datetime = timezone.now()
            other_match.save()
            other_completed_pk = other_match.pk
            other_match.refresh_from_db()
            other_winner_id = other_match.winner_id
            other_status = other_match.status

        remaining_scheduled = (
            Match.objects.filter(
                tournament=self.tournament,
                status=Match.MatchStatus.SCHEDULED,
            )
            .filter(_matches_for_player(leaver))
            .count()
        )
        self.assertGreater(remaining_scheduled, 0)

        ok, msg = withdraw_participant(
            self.tournament, player=leaver, withdrawn_by=self.admin
        )
        self.assertTrue(ok, msg)

        self.subs[0].refresh_from_db()
        self.assertEqual(self.subs[0].fancoin_balance, 0)
        self.assertTrue(
            self.tournament.registration_coverages.filter(user=leaver.user).exists()
        )
        self.assertFalse(
            FancoinTransaction.objects.filter(
                user=leaver.user,
                reason=FancoinTransaction.Reason.TOURNAMENT_WITHDRAWAL,
            ).exists()
        )
        withdrawal = TournamentWithdrawal.objects.get(
            tournament=self.tournament, player=leaver
        )
        self.assertFalse(withdrawal.fancoin_refunded)

        leaver_match.refresh_from_db()
        self.assertEqual(leaver_match.status, Match.MatchStatus.COMPLETED)
        self.assertEqual(leaver_match.winner_id, opponent.pk)

        if other_completed_pk is not None:
            refreshed = Match.objects.get(pk=other_completed_pk)
            self.assertEqual(refreshed.status, other_status)
            self.assertEqual(refreshed.winner_id, other_winner_id)

        self.assertEqual(
            Match.objects.filter(
                tournament=self.tournament,
                status=Match.MatchStatus.SCHEDULED,
            )
            .filter(_matches_for_player(leaver))
            .count(),
            0,
        )

    def test_withdraw_twice_fails(self) -> None:
        """Повторное снятие возвращает ошибку."""
        leaver = self.players[0]
        ok, _ = withdraw_participant(
            self.tournament, player=leaver, withdrawn_by=self.admin
        )
        self.assertTrue(ok)
        ok2, msg2 = withdraw_participant(
            self.tournament, player=leaver, withdrawn_by=self.admin
        )
        self.assertFalse(ok2)
        self.assertIn("уже снят", msg2)

    def test_withdraw_lk_notification_fits_charfield(self) -> None:
        """Длинное имя турнира + список матчей не ломает Notification.message."""
        long_name = "Многодневный турнир " + ("Воскресенск " * 20)
        self.tournament.name = long_name
        self.tournament.save(update_fields=["name"])
        leaver = self.players[0]

        ok, msg = withdraw_participant(
            self.tournament, player=leaver, withdrawn_by=self.admin
        )
        self.assertTrue(ok, msg)

        notif = Notification.objects.filter(user=leaver.user).latest("created_at")
        self.assertLessEqual(len(notif.message), 255)
        self.assertTrue(notif.message.startswith("Вы сняты"))

    def test_truncate_notification_message(self) -> None:
        """Хелпер обрезает текст ровно до max_len."""
        self.assertEqual(_truncate_notification_message("ok"), "ok")
        truncated = _truncate_notification_message("а" * 300)
        self.assertEqual(len(truncated), 255)
        self.assertTrue(truncated.endswith("…"))

    def test_withdraw_does_not_change_fan_rating_or_match_stats(self) -> None:
        """Walkover при снятии не меняет FAN и matches_played/won."""
        leaver = self.players[0]
        opponents = self.players[1:]
        for p in self.players:
            p.hidden_rating = 2500.0 + p.pk
            p.total_points = 2500.0 + p.pk
            p.matches_played = 3
            p.matches_won = 1
            p.save(
                update_fields=[
                    "hidden_rating",
                    "total_points",
                    "matches_played",
                    "matches_won",
                ]
            )

        before = {
            p.pk: (
                float(p.total_points),
                p.matches_played,
                p.matches_won,
            )
            for p in self.players
        }

        ok, msg = withdraw_participant(
            self.tournament, player=leaver, withdrawn_by=self.admin
        )
        self.assertTrue(ok, msg)

        walkovers = Match.objects.filter(
            tournament=self.tournament,
            status=Match.MatchStatus.WALKOVER,
        ).filter(_matches_for_player(leaver))
        self.assertGreater(walkovers.count(), 0)
        for m in walkovers:
            self.assertEqual(m.rating_status, Match.RatingCalcStatus.NOT_APPLICABLE)
            self.assertEqual(m.rating_delta_player1 or 0.0, 0.0)
            self.assertEqual(m.rating_delta_player2 or 0.0, 0.0)

        for p in self.players:
            p.refresh_from_db()
            rating, played, won = before[p.pk]
            self.assertEqual(float(p.total_points), rating)
            self.assertEqual(float(p.hidden_rating), rating)
            self.assertEqual(p.matches_played, played)
            self.assertEqual(p.matches_won, won)

        # Соперники остались с прежним рейтингом (явная проверка по списку)
        for opp in opponents:
            opp.refresh_from_db()
            self.assertEqual(float(opp.total_points), before[opp.pk][0])

    def test_revert_withdrawal_walkover_rating_effects(self) -> None:
        """Откат восстанавливает FAN по всем walkover одного игрока (не только последнему)."""
        from apps.tournaments.withdraw import revert_withdrawal_walkover_rating_effects

        leaver = self.players[0]
        ok, msg = withdraw_participant(
            self.tournament, player=leaver, withdrawn_by=self.admin
        )
        self.assertTrue(ok, msg)

        walkovers = list(
            Match.objects.filter(
                tournament=self.tournament,
                status=Match.MatchStatus.WALKOVER,
            )
            .filter(_matches_for_player(leaver))
            .select_related("player1", "player2", "winner")
            .order_by("pk")
        )
        self.assertGreaterEqual(len(walkovers), 2)

        leaver.total_points = 2000.0
        leaver.hidden_rating = 2000.0
        leaver.matches_played = 10
        leaver.matches_won = 0
        leaver.save(
            update_fields=[
                "total_points",
                "hidden_rating",
                "matches_played",
                "matches_won",
            ]
        )

        expected_rating = 2000.0
        for i, walkover in enumerate(walkovers):
            if walkover.player1_id == leaver.pk:
                d1, d2 = -50.0 - i * 20.0, 40.0
                expected_rating -= d1
            else:
                d1, d2 = 40.0, -50.0 - i * 20.0
                expected_rating -= d2
            Match.objects.filter(pk=walkover.pk).update(
                rating_status=Match.RatingCalcStatus.CALCULATED,
                rating_delta_player1=d1,
                rating_delta_player2=d2,
            )
            opp = (
                walkover.player2
                if walkover.player1_id == leaver.pk
                else walkover.player1
            )
            assert opp is not None
            opp.matches_played = max(opp.matches_played, 5)
            opp.matches_won = max(opp.matches_won, 2)
            opp.save(update_fields=["matches_played", "matches_won"])

        fixed, _ = revert_withdrawal_walkover_rating_effects(tournament=self.tournament)
        self.assertEqual(fixed, len(walkovers))

        leaver.refresh_from_db()
        self.assertAlmostEqual(float(leaver.total_points), expected_rating, places=1)
        self.assertEqual(leaver.matches_played, 10 - len(walkovers))

        for walkover in walkovers:
            walkover.refresh_from_db()
            self.assertEqual(
                walkover.rating_status, Match.RatingCalcStatus.NOT_APPLICABLE
            )

    def test_finalize_skips_place_points_for_withdrawn(self) -> None:
        """Снятый игрок не получает сезонные очки за место при финализации."""
        leaver = self.players[0]
        ok, _ = withdraw_participant(
            self.tournament, player=leaver, withdrawn_by=self.admin
        )
        self.assertTrue(ok)

        for match in Match.objects.filter(
            tournament=self.tournament, status=Match.MatchStatus.SCHEDULED
        ):
            match.winner = match.player1
            match.status = Match.MatchStatus.COMPLETED
            match.completed_datetime = timezone.now()
            match.player1_set1 = 6
            match.player2_set1 = 0
            match.player1_set2 = 6
            match.player2_set2 = 1
            match.save()

        finalized = check_and_finalize_if_complete(self.tournament)
        self.assertTrue(finalized)
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.COMPLETED)

        result = TournamentPlayerResult.objects.filter(
            tournament=self.tournament, player=leaver
        ).first()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.fan_points, 0)

        # У остальных есть места; топ-3 получают ненулевые очки за место (кубки по place 1–3)
        others = TournamentPlayerResult.objects.filter(
            tournament=self.tournament
        ).exclude(player=leaver)
        self.assertEqual(others.count(), 3)
        places = sorted(others.values_list("place", flat=True))
        self.assertEqual(places, [1, 2, 3])
        self.assertTrue(others.filter(place=1, fan_points__gt=0).exists())

        from apps.tournaments.utils import get_players_trophies_map

        trophies = get_players_trophies_map(
            [p.pk for p in self.players if p.pk != leaver.pk]
        )
        # Хотя бы у победителя есть трофей за 1 место
        winner = others.get(place=1)
        winner_trophies = trophies.get(winner.player_id) or []
        self.assertTrue(
            any(getattr(t, "place", None) == 1 for t in winner_trophies),
            f"Ожидался кубок 1 места, получено: {winner_trophies}",
        )
