"""Интеграционные тесты: финализация платежей, вебхук и return YooKassa."""

from __future__ import annotations

import json
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    ClubMember,
    ClubMemberPlan,
    ClubMemberRole,
    ClubMemberStatus,
    ClubPlayerPlan,
)
from apps.payments.models import PaymentRecord, SavedPaymentMethod
from apps.payments.views import finalize_successful_payment
from apps.subscriptions.models import SubscriptionTier, UserSubscription
from apps.tournaments.models import (
    TournamentEntryPayment,
    TournamentFormat,
    TournamentGender,
    TournamentStatus,
)
from apps.users.models import Player
from tests.support.factories import (
    make_club,
    make_payment_record,
    make_tournament,
    make_user,
)


@contextmanager
def mute_payment_side_effects():
    """Отключить внешние уведомления при финализации платежа."""
    with (
        patch("apps.core.telegram_notify.notify_subscription_purchase"),
        patch("apps.core.telegram_notify.notify_tournament_entry_payment"),
        patch("apps.core.telegram_notify.notify_donation"),
        patch("apps.subscriptions.utils.send_subscription_purchase_email"),
        patch("apps.core.email_service.send_tournament_entry_receipt_email"),
        patch("apps.core.email_service.send_club_plan_receipt_email"),
        patch("apps.core.email_service.send_donation_thanks_email"),
    ):
        yield


class FinalizeSuccessfulPaymentTestCase(TestCase):
    """Выдача услуг после успешной оплаты."""

    def test_finalize_skips_when_status_not_succeeded(self) -> None:
        user = make_user(email="pending-pay@test.local")
        tier = SubscriptionTier.objects.create(
            name="skip-tier",
            display_name="Skip",
            price=Decimal("1000.00"),
            duration_days=30,
            is_visible=True,
        )
        record = make_payment_record(
            user,
            payment_type=PaymentRecord.PaymentType.SUBSCRIPTION,
            item_id=str(tier.id),
            status="pending",
        )

        with mute_payment_side_effects():
            finalize_successful_payment(record)

        self.assertFalse(UserSubscription.objects.filter(user=user).exists())

    def test_finalize_subscription_activates_and_adds_fancoin(self) -> None:
        user = make_user(email="sub-pay@test.local")
        Player.objects.create(user=user, city="Москва")
        tier = SubscriptionTier.objects.create(
            name="paid-tier",
            display_name="Paid",
            price=Decimal("2000.00"),
            fancoin_per_purchase=25,
            duration_days=30,
            is_visible=True,
        )
        record = make_payment_record(
            user,
            yookassa_payment_id="sub-finalize-001",
            payment_type=PaymentRecord.PaymentType.SUBSCRIPTION,
            item_id=str(tier.id),
            amount=Decimal("2000.00"),
        )

        with mute_payment_side_effects():
            finalize_successful_payment(record)

        subscription = UserSubscription.objects.get(user=user)
        self.assertTrue(subscription.is_valid())
        self.assertEqual(subscription.tier_id, tier.id)
        self.assertEqual(subscription.fancoin_balance, 25)
        self.assertEqual(subscription.purchase_city, "moscow")
        user.player.refresh_from_db()
        self.assertTrue(user.player.has_ever_paid_subscription)

    def test_finalize_subscription_extends_existing_end_date(self) -> None:
        user = make_user(email="extend-sub@test.local")
        tier = SubscriptionTier.objects.create(
            name="extend-tier",
            display_name="Extend",
            price=Decimal("1500.00"),
            fancoin_per_purchase=0,
            duration_days=10,
            is_visible=True,
        )
        old_end = timezone.now() + timezone.timedelta(days=3)
        UserSubscription.objects.create(
            user=user,
            tier=tier,
            start_date=timezone.now() - timezone.timedelta(days=20),
            end_date=old_end,
            is_active=True,
        )
        record = make_payment_record(
            user,
            yookassa_payment_id="sub-finalize-002",
            payment_type=PaymentRecord.PaymentType.SUBSCRIPTION,
            item_id=str(tier.id),
        )

        with mute_payment_side_effects():
            finalize_successful_payment(record)

        subscription = UserSubscription.objects.get(user=user)
        self.assertGreater(subscription.end_date, old_end)

    def test_finalize_tournament_registers_singles_player(self) -> None:
        user = make_user(email="tour-pay@test.local")
        player = Player.objects.create(user=user, gender="male")
        tournament = make_tournament(
            name="Оплачиваемый турнир",
            slug="paid-tournament",
            gender=TournamentGender.MALE,
            format=TournamentFormat.SINGLE_ELIMINATION,
            status=TournamentStatus.UPCOMING,
        )
        record = make_payment_record(
            user,
            yookassa_payment_id="tour-finalize-001",
            payment_type=PaymentRecord.PaymentType.TOURNAMENT,
            item_id=str(tournament.id),
            amount=Decimal("500.00"),
        )

        with mute_payment_side_effects():
            finalize_successful_payment(record)

        self.assertTrue(tournament.participants.filter(pk=player.pk).exists())
        self.assertTrue(
            TournamentEntryPayment.objects.filter(
                tournament=tournament,
                user=user,
            ).exists()
        )

    def test_finalize_club_plan_assigns_active_member_plan(self) -> None:
        user = make_user(email="club-plan-pay@test.local")
        club = make_club(name="Клуб оплаты", slug="pay-club")
        ClubMember.objects.create(
            club=club,
            user=user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        plan = ClubPlayerPlan.objects.create(
            club=club,
            name="Стандарт",
            monthly_fee=Decimal("900.00"),
            max_tournaments_per_month=4,
            is_active=True,
        )
        record = make_payment_record(
            user,
            yookassa_payment_id="club-plan-finalize-001",
            payment_type=PaymentRecord.PaymentType.CLUB_PLAN,
            item_id=str(plan.id),
            metadata={"enable_autopay": "1"},
        )

        with mute_payment_side_effects():
            finalize_successful_payment(record)

        assignment = ClubMemberPlan.objects.get(
            club_member__user=user,
            club_member__club=club,
            status="active",
        )
        self.assertEqual(assignment.plan_id, plan.id)
        self.assertTrue(assignment.auto_renew)

    def test_finalize_saves_subscription_payment_method_on_autopay(self) -> None:
        user = make_user(email="autopay-sub@test.local")
        tier = SubscriptionTier.objects.create(
            name="autopay-tier",
            display_name="Autopay",
            price=Decimal("1000.00"),
            duration_days=30,
            is_visible=True,
        )
        record = make_payment_record(
            user,
            yookassa_payment_id="autopay-sub-001",
            payment_type=PaymentRecord.PaymentType.SUBSCRIPTION,
            item_id=str(tier.id),
            metadata={"enable_autopay": "1"},
        )
        details = {
            "payment_method": {
                "id": "pm-sub-test-001",
                "saved": True,
                "card": {
                    "last4": "4242",
                    "expiry_month": "12",
                    "expiry_year": "2030",
                    "card_type": "Visa",
                },
            }
        }

        with (
            mute_payment_side_effects(),
            patch(
                "apps.payments.yookassa_client.get_payment_details",
                return_value=details,
            ),
        ):
            finalize_successful_payment(record)

        saved = SavedPaymentMethod.objects.get(payment_method_id="pm-sub-test-001")
        self.assertEqual(saved.user_id, user.id)
        self.assertTrue(saved.is_default_for_subscriptions)


class YookassaWebhookTestCase(TestCase):
    """Вебхук подтверждения успешного платежа."""

    def setUp(self) -> None:
        self.client = Client()

    def _post_webhook(self, payment_id: str) -> int:
        response = self.client.post(
            reverse("payment_webhook"),
            data=json.dumps(
                {
                    "event": "payment.succeeded",
                    "object": {"id": payment_id},
                }
            ),
            content_type="application/json",
            secure=True,
        )
        return response.status_code

    def test_webhook_finalizes_pending_platform_payment(self) -> None:
        user = make_user(email="webhook@test.local")
        tier = SubscriptionTier.objects.create(
            name="webhook-tier",
            display_name="Webhook",
            price=Decimal("800.00"),
            fancoin_per_purchase=5,
            duration_days=30,
            is_visible=True,
        )
        payment_id = "webhook-pay-001"
        record = make_payment_record(
            user,
            yookassa_payment_id=payment_id,
            payment_type=PaymentRecord.PaymentType.SUBSCRIPTION,
            item_id=str(tier.id),
            status="pending",
        )

        with (
            mute_payment_side_effects(),
            patch(
                "apps.payments.yookassa_client.get_payment_status",
                return_value="succeeded",
            ),
        ):
            status_code = self._post_webhook(payment_id)

        self.assertEqual(status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.status, "succeeded")
        self.assertTrue(UserSubscription.objects.filter(user=user).exists())

    def test_webhook_is_idempotent_for_already_succeeded_payment(self) -> None:
        user = make_user(email="webhook-idem@test.local")
        tier = SubscriptionTier.objects.create(
            name="idem-tier",
            display_name="Idem",
            price=Decimal("500.00"),
            fancoin_per_purchase=10,
            duration_days=30,
            is_visible=True,
        )
        payment_id = "webhook-pay-002"
        make_payment_record(
            user,
            yookassa_payment_id=payment_id,
            payment_type=PaymentRecord.PaymentType.SUBSCRIPTION,
            item_id=str(tier.id),
            status="succeeded",
        )
        UserSubscription.objects.create(
            user=user,
            tier=tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timezone.timedelta(days=30),
            fancoin_balance=10,
            is_active=True,
        )

        with patch(
            "apps.payments.yookassa_client.get_payment_status",
            return_value="succeeded",
        ) as get_status:
            status_code = self._post_webhook(payment_id)

        self.assertEqual(status_code, 200)
        get_status.assert_not_called()
        subscription = UserSubscription.objects.get(user=user)
        self.assertEqual(subscription.fancoin_balance, 10)

    def test_webhook_returns_ok_for_unknown_payment_id(self) -> None:
        with patch("apps.payments.yookassa_client.get_payment_status") as get_status:
            status_code = self._post_webhook("missing-payment-id")

        self.assertEqual(status_code, 200)
        get_status.assert_not_called()


class PaymentReturnFinalizeTestCase(TestCase):
    """Return URL подтверждает pending-платёж платформы."""

    def setUp(self) -> None:
        self.user = make_user(email="return-pay@test.local")
        self.tier = SubscriptionTier.objects.create(
            name="return-tier",
            display_name="Return",
            price=Decimal("1200.00"),
            fancoin_per_purchase=3,
            duration_days=30,
            is_visible=True,
        )

    def test_payment_return_finalizes_pending_record(self) -> None:
        from django.contrib.sessions.backends.db import SessionStore

        from apps.payments.views import payment_return

        payment_id = "return-pay-001"
        make_payment_record(
            self.user,
            yookassa_payment_id=payment_id,
            payment_type=PaymentRecord.PaymentType.SUBSCRIPTION,
            item_id=str(self.tier.id),
            status="pending",
        )
        session = SessionStore()
        session["yookassa_pending"] = {
            "payment_id": payment_id,
            "payment_type": "subscription",
            "item_id": str(self.tier.id),
            "next": "",
        }
        session.save()

        request = RequestFactory().get("/payments/return/", secure=True)
        request.user = self.user
        request.session = session

        with (
            mute_payment_side_effects(),
            patch(
                "apps.payments.yookassa_client.get_payment_status",
                return_value="succeeded",
            ),
        ):
            response = payment_return(request)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("payment_success"), response.url)
        record = PaymentRecord.objects.get(yookassa_payment_id=payment_id)
        self.assertEqual(record.status, "succeeded")
        self.assertTrue(UserSubscription.objects.filter(user=self.user).exists())
