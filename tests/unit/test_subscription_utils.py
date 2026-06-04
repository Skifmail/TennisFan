"""Юнит-тесты: утилиты подписок и проверки прав по тарифу."""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.subscriptions.models import (
    RegionalTierPrice,
    SubscriptionTier,
    UserSubscription,
)
from apps.subscriptions.utils import (
    get_subscription_renew_amount,
    normalize_city_for_pricing,
    user_can_rate_opponents,
    user_can_read_comments,
    user_can_write_comments,
)
from tests.support.factories import make_user


class NormalizeCityForPricingTestCase(TestCase):
    """Нормализация города для московского и регионального прайсинга."""

    def test_moscow_variants_map_to_moscow(self) -> None:
        for city in ("Москва", "moscow", "MOSKVA", "  москва  "):
            with self.subTest(city=city):
                self.assertEqual(normalize_city_for_pricing(city), "moscow")

    def test_empty_defaults_to_moscow(self) -> None:
        self.assertEqual(normalize_city_for_pricing(""), "moscow")

    def test_regional_city_is_lowercased(self) -> None:
        self.assertEqual(normalize_city_for_pricing("Казань"), "казань")


class GetSubscriptionRenewAmountTestCase(TestCase):
    """Расчёт суммы продления с учётом региона покупки."""

    def setUp(self) -> None:
        self.tier = SubscriptionTier.objects.create(
            name="renew-tier",
            display_name="Renew",
            price=Decimal("5000.00"),
            duration_days=30,
            is_visible=True,
        )
        self.user = make_user(email="renew@test.local")
        RegionalTierPrice.objects.create(
            tier=self.tier,
            name="Регионы",
            price=Decimal("3500.00"),
        )

    def test_moscow_uses_base_tier_price(self) -> None:
        subscription = UserSubscription.objects.create(
            user=self.user,
            tier=self.tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timezone.timedelta(days=30),
            purchase_city="moscow",
            is_active=True,
        )

        self.assertEqual(
            get_subscription_renew_amount(subscription), Decimal("5000.00")
        )

    def test_regional_city_uses_regional_price(self) -> None:
        subscription = UserSubscription.objects.create(
            user=self.user,
            tier=self.tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timezone.timedelta(days=30),
            purchase_city="kazan",
            is_active=True,
        )

        self.assertEqual(
            get_subscription_renew_amount(subscription), Decimal("3500.00")
        )


class SubscriptionPermissionHelpersTestCase(TestCase):
    """Права пользователя зависят от активного тарифа."""

    def setUp(self) -> None:
        self.user = make_user(email="perms@test.local")
        self.tier = SubscriptionTier.objects.create(
            name="perms-tier",
            display_name="Perms",
            price=Decimal("1000.00"),
            duration_days=30,
            can_read_comments=True,
            can_write_comments=True,
            can_rate_opponents=True,
            is_visible=True,
        )

    def test_active_subscription_grants_features(self) -> None:
        UserSubscription.objects.create(
            user=self.user,
            tier=self.tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timezone.timedelta(days=10),
            is_active=True,
        )

        self.assertTrue(user_can_read_comments(self.user))
        self.assertTrue(user_can_write_comments(self.user))
        self.assertTrue(user_can_rate_opponents(self.user))

    def test_expired_subscription_denies_features(self) -> None:
        UserSubscription.objects.create(
            user=self.user,
            tier=self.tier,
            start_date=timezone.now() - timezone.timedelta(days=60),
            end_date=timezone.now() - timezone.timedelta(days=1),
            is_active=True,
        )

        self.assertFalse(user_can_read_comments(self.user))
        self.assertFalse(user_can_write_comments(self.user))
        self.assertFalse(user_can_rate_opponents(self.user))

    def test_anonymous_user_has_no_permissions(self) -> None:
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(user_can_read_comments(AnonymousUser()))
