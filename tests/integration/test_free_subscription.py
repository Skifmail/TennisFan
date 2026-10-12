"""Бессрочный тариф Free и возврат на него после окончания платного."""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.sparring.utils import user_has_sparring_access
from apps.subscriptions.models import SubscriptionTier, UserSubscription
from apps.subscriptions.services import ensure_current_subscription
from apps.subscriptions.utils import user_can_write_comments
from tests.support.factories import make_player, make_user


class FreeSubscriptionTests(TestCase):
    """Free назначается всем, его галочки действуют, платный срок возвращает на Free."""

    def setUp(self) -> None:
        self.free = SubscriptionTier.objects.create(
            name="free",
            display_name="Free",
            price=0,
            has_sparring=False,
            can_write_comments=False,
        )
        self.user = make_user(email="free-user@test.local")

    def test_new_user_gets_perpetual_free(self) -> None:
        """Новый пользователь сразу получает бессрочный Free."""
        subscription = UserSubscription.objects.get(user=self.user)

        self.assertEqual(subscription.tier_id, self.free.pk)
        self.assertIsNone(subscription.end_date)
        self.assertTrue(subscription.is_valid())
        self.assertEqual(subscription.status_display, "Бессрочно")

    def test_free_tier_flags_apply(self) -> None:
        """Галочки тарифа Free открывают и закрывают возможности."""
        self.assertFalse(user_has_sparring_access(self.user))
        self.assertFalse(user_can_write_comments(self.user))

        self.free.has_sparring = True
        self.free.can_write_comments = True
        self.free.save(update_fields=["has_sparring", "can_write_comments"])
        self.user.subscription.tier.refresh_from_db()

        self.assertTrue(user_has_sparring_access(self.user))
        self.assertTrue(user_can_write_comments(self.user))

    def test_active_paid_tier_is_kept(self) -> None:
        """Действующий платный тариф не заменяется бесплатным."""
        paid = SubscriptionTier.objects.create(
            name="silver",
            display_name="Silver",
            has_sparring=True,
        )
        subscription = self.user.subscription
        subscription.tier = paid
        subscription.end_date = timezone.now() + timezone.timedelta(days=10)
        subscription.fancoin_balance = 4
        subscription.save()

        current = ensure_current_subscription(self.user)

        assert current is not None
        self.assertEqual(current.tier_id, paid.pk)
        self.assertEqual(current.fancoin_balance, 4)
        self.assertTrue(user_has_sparring_access(self.user))

    def test_expired_paid_tier_returns_to_free_and_keeps_fancoin(self) -> None:
        """После окончания платного тарифа снова действует Free и его галочки."""
        paid = SubscriptionTier.objects.create(
            name="gold",
            display_name="Gold",
            has_sparring=True,
        )
        self.free.has_sparring = False
        self.free.save(update_fields=["has_sparring"])
        subscription = self.user.subscription
        subscription.tier = paid
        subscription.end_date = timezone.now() - timezone.timedelta(days=1)
        subscription.fancoin_balance = 9
        subscription.cancelled_at = timezone.now()
        subscription.save()

        current = ensure_current_subscription(self.user)

        assert current is not None
        self.assertEqual(current.tier_id, self.free.pk)
        self.assertIsNone(current.end_date)
        self.assertIsNone(current.cancelled_at)
        self.assertTrue(current.is_valid())
        self.assertEqual(current.fancoin_balance, 9)
        self.assertFalse(user_has_sparring_access(self.user))

    def test_free_tier_cannot_be_cancelled(self) -> None:
        """Бесплатный тариф нельзя отменить."""
        player = make_player(email_suffix="free-cancel")
        self.client.force_login(player.user)

        response = self.client.post(reverse("cancel_subscription"), secure=True)

        self.assertRedirects(
            response,
            reverse("profile", args=[player.pk]),
            fetch_redirect_response=False,
        )
        subscription = UserSubscription.objects.get(user=player.user)
        self.assertEqual(subscription.tier_id, self.free.pk)
        self.assertIsNone(subscription.cancelled_at)
        self.assertIsNone(subscription.end_date)
