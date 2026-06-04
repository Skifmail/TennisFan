"""Интеграционные тесты: модель подписки и срок действия тарифа."""

from django.test import TestCase
from django.utils import timezone

from apps.subscriptions.models import SubscriptionTier, UserSubscription
from tests.support.factories import make_user


class UserSubscriptionValidityTestCase(TestCase):
    """Проверка ``is_valid`` и продления срока по тарифу."""

    def setUp(self) -> None:
        self.user = make_user(email="sub-valid@test.local")
        self.tier = SubscriptionTier.objects.create(
            name="valid-tier",
            display_name="Valid",
            price=0,
            duration_days=14,
            is_visible=True,
        )

    def test_is_valid_for_active_future_subscription(self) -> None:
        subscription = UserSubscription.objects.create(
            user=self.user,
            tier=self.tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timezone.timedelta(days=5),
            is_active=True,
        )

        self.assertTrue(subscription.is_valid())

    def test_is_invalid_when_end_date_passed(self) -> None:
        subscription = UserSubscription.objects.create(
            user=self.user,
            tier=self.tier,
            start_date=timezone.now() - timezone.timedelta(days=30),
            end_date=timezone.now() - timezone.timedelta(days=1),
            is_active=True,
        )

        self.assertFalse(subscription.is_valid())

    def test_cancelled_subscription_stays_valid_until_end_date(self) -> None:
        subscription = UserSubscription.objects.create(
            user=self.user,
            tier=self.tier,
            start_date=timezone.now() - timezone.timedelta(days=2),
            end_date=timezone.now() + timezone.timedelta(days=5),
            is_active=True,
            cancelled_at=timezone.now(),
        )

        self.assertTrue(subscription.is_valid())

    def test_apply_duration_extends_from_base_datetime(self) -> None:
        base = timezone.now()
        expected_end = self.tier.apply_duration(base)

        self.assertEqual(
            (expected_end - base).days,
            self.tier.duration_days,
        )
