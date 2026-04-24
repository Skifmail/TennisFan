"""Тесты доменной логики FANcoin."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.subscriptions.fancoin import (
    SPARRING_SINGLES_COST,
    TOURNAMENT_REGISTRATION_COST,
)
from apps.subscriptions.models import (
    FancoinTransaction,
    SubscriptionTier,
    UserSubscription,
)
from apps.subscriptions.sparring_billing import charge_fancoin_for_completed_match
from apps.tournaments.models import Match
from apps.users.models import Player


class FancoinFlowTests(TestCase):
    """Проверить базовые сценарии работы FANcoin."""

    def setUp(self) -> None:
        """Подготовить базовые объекты для тестов.

        Args:
            None: Используется контекст тестового класса.

        Returns:
            None: Создаёт тариф и пользователей.
        """
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            email="first@example.com",
            password="pass1234",
        )
        self.user2 = user_model.objects.create_user(
            email="second@example.com",
            password="pass1234",
        )
        self.tier = SubscriptionTier.objects.create(
            name="test",
            display_name="Test",
            fancoin_per_purchase=15,
            duration_days=30,
            is_visible=True,
        )
        self.subscription = UserSubscription.objects.create(
            user=self.user,
            tier=self.tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
            fancoin_balance=0,
            is_active=True,
        )

    def test_add_and_spend_fancoin(self) -> None:
        """Проверить начисление и списание FANcoin.

        Args:
            None: Используется контекст теста.

        Returns:
            None: Проверки выполняются через assert.
        """
        self.subscription.add_fancoin(self.tier.fancoin_per_purchase)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.fancoin_balance, 15)

        spent = self.subscription.spend_fancoin(
            TOURNAMENT_REGISTRATION_COST,
            reason=FancoinTransaction.Reason.TOURNAMENT_REGISTRATION,
        )
        self.assertTrue(spent)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.fancoin_balance, 12)

    def test_spend_insufficient_balance(self) -> None:
        """Проверить отказ списания при недостатке FANcoin.

        Args:
            None: Используется контекст теста.

        Returns:
            None: Проверки выполняются через assert.
        """
        self.subscription.fancoin_balance = 2
        self.subscription.save(update_fields=["fancoin_balance"])
        spent = self.subscription.spend_fancoin(
            TOURNAMENT_REGISTRATION_COST,
            reason=FancoinTransaction.Reason.TOURNAMENT_REGISTRATION,
        )
        self.assertFalse(spent)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.fancoin_balance, 2)

    def test_charge_singles_sparring(self) -> None:
        """Проверить списание FANcoin после завершённого одиночного спарринга.

        Args:
            None: Используется контекст теста.

        Returns:
            None: Проверки выполняются через assert.
        """
        sub2 = UserSubscription.objects.create(
            user=self.user2,
            tier=self.tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
            fancoin_balance=10,
            is_active=True,
        )
        self.subscription.fancoin_balance = 10
        self.subscription.save(update_fields=["fancoin_balance"])

        player1 = Player.objects.create(user=self.user)
        player2 = Player.objects.create(user=self.user2)
        match = Match.objects.create(
            match_type=Match.MatchType.SPARRING,
            player1=player1,
            player2=player2,
            status=Match.MatchStatus.COMPLETED,
            completed_datetime=timezone.now(),
        )
        charge_fancoin_for_completed_match(match)
        self.subscription.refresh_from_db()
        sub2.refresh_from_db()
        self.assertEqual(self.subscription.fancoin_balance, 10 - SPARRING_SINGLES_COST)
        self.assertEqual(sub2.fancoin_balance, 10 - SPARRING_SINGLES_COST)
