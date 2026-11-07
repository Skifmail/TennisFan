"""Однократное списание серии по каждому игроку и откат позднего сбоя журнала."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from apps.notifications.models import Delivery
from apps.sparring.doubles_services import (
    accept_join_request,
    confirm_team_sparring_series,
    create_doubles_request,
    create_join_request,
)
from apps.sparring.models import TeamSide
from apps.subscriptions.fancoin import SPARRING_TEAM_COST
from apps.subscriptions.models import (
    FancoinTransaction,
    SubscriptionTier,
    UserSubscription,
)
from apps.subscriptions.sparring_billing import charge_fancoin_for_completed_match
from apps.tournaments.models import Match
from apps.tournaments.proposal_service import submit_match_result
from apps.users.models import Notification
from tests.support.factories import make_player, make_subscription


@override_settings(
    NOTIFICATION_CENTER_ENABLED=False, USER_NOTIFICATIONS_EMAIL_ENABLED=False
)
class SparringBillingAtomicityTests(TestCase):
    """Результат, балансы и история фиксируются одной транзакцией."""

    def setUp(self) -> None:
        """Подготовить настоящую серию и четыре независимых баланса."""
        self.players = [
            make_player(email_suffix=f"billing-series-{index}", points=2500)
            for index in range(4)
        ]
        self.subscriptions = [
            make_subscription(player.user, fancoin_balance=20)
            for player in self.players
        ]
        self.request = create_doubles_request(
            created_by=self.players[0], partner=self.players[1], kind="team"
        )
        join = create_join_request(
            match_request_id=self.request.pk,
            created_by=self.players[2],
            target_side=TeamSide.OPPONENT,
            players=self.players[2:],
        )
        accept_join_request(
            match_request_id=self.request.pk,
            join_request_id=join.pk,
            accepted_by=self.players[0],
        )
        self.matches = confirm_team_sparring_series(self.request.pk, self.players[0])
        Delivery.objects.all().delete()
        Notification.objects.all().delete()
        mail.outbox.clear()

    def _complete(self, match: Match) -> None:
        """Применить результат через тот же сервис, который вызывают сайт и бот."""
        player = match.player1
        assert player is not None
        submit_match_result(
            match.pk,
            player,
            result=Match.ResultChoice.WIN,
            player1_set1=6,
            player2_set1=4,
        )

    def _balances(self) -> list[int]:
        """Получить актуальные балансы по стабильному порядку пользователей."""
        return list(
            UserSubscription.objects.order_by("user_id").values_list(
                "fancoin_balance", flat=True
            )
        )

    def test_partial_legacy_charge_does_not_skip_other_three_players(self) -> None:
        """Одна прежняя запись не означает, что вся серия уже оплачена."""
        first = self.subscriptions[0]
        first.fancoin_balance = 20 - SPARRING_TEAM_COST
        first.save(update_fields=["fancoin_balance"])
        FancoinTransaction.objects.create(
            user=first.user,
            match=self.matches[0],
            doubles_request=self.request,
            amount=SPARRING_TEAM_COST,
            direction="charge",
            reason="sparring_team",
            balance_after=first.fancoin_balance,
        )
        self._complete(self.matches[1])
        self._complete(self.matches[3])
        self.assertEqual(self._balances(), [20 - SPARRING_TEAM_COST] * 4)
        self.assertEqual(
            FancoinTransaction.objects.filter(doubles_request=self.request).count(), 4
        )

    def test_late_transaction_history_failure_rolls_back_result_and_all_balances(
        self,
    ) -> None:
        """Отказ четвёртой записи возвращает первые три баланса, рейтинг и результат."""
        original = FancoinTransaction.objects.create

        def create(**kwargs: Any) -> FancoinTransaction:
            """Отказать последнему игроку после реальных первых списаний."""
            if kwargs["user"].pk == self.players[-1].user_id:
                raise RuntimeError("late billing history failure")
            return original(**kwargs)

        with patch(
            "apps.subscriptions.models.FancoinTransaction.objects.create",
            side_effect=create,
        ):
            with self.assertRaises(RuntimeError):
                self._complete(self.matches[0])
        self.assertEqual(self._balances(), [20] * 4)
        self.assertFalse(FancoinTransaction.objects.exists())
        self.assertFalse(self.matches[0].result_proposals.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(mail.outbox)
        self.assertEqual(
            set(Match.objects.values_list("status", flat=True)),
            {Match.MatchStatus.SCHEDULED},
        )
        for player in self.players:
            player.refresh_from_db()
            self.assertEqual(player.matches_played, 0)
            self.assertEqual(player.total_points, 2500)
        self._complete(self.matches[0])
        self.assertEqual(self._balances(), [20 - SPARRING_TEAM_COST] * 4)

    def test_unlimited_series_is_not_charged_after_tier_downgrade(self) -> None:
        """Безлимит закрывает оплату серии, а смена тарифа не списывает её задним числом."""
        unlimited = SubscriptionTier.objects.create(
            name="billing-unlimited", display_name="Безлимит", is_unlimited=True
        )
        UserSubscription.objects.update(tier=unlimited)
        self._complete(self.matches[0])
        self.assertEqual(self._balances(), [20] * 4)
        UserSubscription.objects.update(tier=self.subscriptions[0].tier)
        self._complete(self.matches[3])
        self.assertEqual(self._balances(), [20] * 4)
        self.assertFalse(FancoinTransaction.objects.filter(amount__gt=0).exists())

    def test_unlimited_single_match_is_not_charged_after_tier_downgrade(self) -> None:
        """Повторное списание самого матча также не зависит от текущего безлимита."""
        match = Match.objects.create(
            match_type=Match.MatchType.SPARRING,
            player1=self.players[0],
            player2=self.players[1],
        )
        unlimited = SubscriptionTier.objects.create(
            name="billing-single-unlimited", display_name="Безлимит", is_unlimited=True
        )
        UserSubscription.objects.update(tier=unlimited)
        self._complete(match)
        UserSubscription.objects.update(tier=self.subscriptions[0].tier)
        charge_fancoin_for_completed_match(match)
        self.assertEqual(self._balances(), [20] * 4)
        self.assertFalse(
            FancoinTransaction.objects.filter(match=match, amount__gt=0).exists()
        )
