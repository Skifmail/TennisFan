"""Бессрочный тариф Free и возврат на него после окончания платного."""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.sparring.utils import user_has_sparring_access
from apps.subscriptions.models import SubscriptionTier, UserSubscription
from apps.subscriptions.services import ensure_current_subscription
from apps.subscriptions.utils import user_can_write_comments
from apps.users.forms import PlayerProfileForm
from tests.support.factories import make_player, make_user


def _profile_data(*, city: str) -> dict[str, str]:
    """Минимальные поля формы профиля для проверки смены населённого пункта."""
    return {
        "first_name": "Александр",
        "last_name": "Шатайло",
        "phone": "+79381138222",
        "birth_date": "1990-09-24",
        "gender": "male",
        "forehand": "right",
        "city": city,
        "bio": "",
        "telegram": "",
        "whatsapp": "",
        "max_contact": "",
    }


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

    def test_free_clears_regional_purchase_city(self) -> None:
        """На Free не остаётся город прошлой оплаты, иначе Москву нельзя выбрать."""
        subscription = self.user.subscription
        subscription.purchase_city = "сочи"
        subscription.save(update_fields=["purchase_city"])

        current = ensure_current_subscription(self.user)

        assert current is not None
        self.assertEqual(current.purchase_city, "")

    def test_free_can_change_city_to_moscow(self) -> None:
        """Бессрочный Free не блокирует смену населённого пункта на Москву."""
        player = make_player(
            email_suffix="city-free",
            first_name="Александр",
            last_name="Шатайло",
        )
        player.city = "Сочи"
        player.birth_date = timezone.now().date().replace(year=1990)
        player.gender = "male"
        player.forehand = "right"
        player.save()
        subscription = player.user.subscription
        subscription.purchase_city = "сочи"
        subscription.save(update_fields=["purchase_city"])

        form = PlayerProfileForm(
            instance=player,
            user=player.user,
            data=_profile_data(city="Москва"),
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_paid_regional_subscription_blocks_moscow(self) -> None:
        """Действующий платный региональный тариф по-прежнему не пускает в Москву."""
        player = make_player(
            email_suffix="city-paid",
            first_name="Александр",
            last_name="Шатайло",
        )
        paid = SubscriptionTier.objects.create(
            name="silver",
            display_name="Silver",
            duration_days=30,
        )
        subscription = player.user.subscription
        subscription.tier = paid
        subscription.end_date = timezone.now() + timezone.timedelta(days=10)
        subscription.purchase_city = "сочи"
        subscription.save()

        form = PlayerProfileForm(
            instance=player,
            user=player.user,
            data=_profile_data(city="Москва"),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("региональному тарифу", form.errors.as_text())

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
