"""E2E: оплаты и изоляция платежей клуба."""

import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    Club,
    ClubFeePayment,
    ClubFeePaymentPending,
    ClubLegalDocument,
    ClubMember,
    ClubMemberBalanceTransaction,
    ClubMemberPlan,
    ClubMemberRole,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubPlayerPlan,
    FeePaymentMethod,
)
from apps.clubs.plan_services import (
    purchase_member_plan,
)
from apps.clubs.services import get_current_period_label
from apps.payments.models import PaymentRecord, SavedPaymentMethod
from apps.users.models import User


class ClubPaymentIsolationTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="club-billing@test.local",
            password="testpass123",
        )
        self.club = Club.objects.create(
            name="Спартак",
            slug="spartak-billing",
            city="Москва",
            address="ул. Спортивная, 1",
            email="billing@test.local",
            admin_name="Администратор клуба",
        )
        self.member = ClubMember.objects.create(
            club=self.club,
            user=self.user,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
            balance=Decimal("0.00"),
        )
        self.payment_settings = ClubMembershipFee.objects.create(
            club=self.club,
            amount=Decimal("300.00"),
            currency="RUB",
            period="monthly",
            period_start_day=1,
            payment_provider=ClubMembershipFee.PaymentProvider.YOOKASSA,
            payment_shop_id="club-shop-001",
            payment_api_key="club-secret-key",
            is_active=True,
        )
        self.plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Платина",
            monthly_fee=Decimal("1200.00"),
            max_tournaments_per_month=6,
            is_active=True,
        )
        ClubLegalDocument.objects.create(
            club=self.club,
            title="Оферта тестового клуба",
            content="Текст оферты",
            version="1.0",
            is_published=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["current_club_slug"] = self.club.slug
        session.save()

    def test_club_plan_payment_process_uses_club_yookassa_credentials(self) -> None:
        with (
            patch(
                "apps.clubs.views.subscription.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.views.subscription.create_payment_with_credentials",
                return_value=("club-plan-pay-001", "https://pay.example/club-plan"),
            ) as create_club_payment,
            patch(
                "apps.clubs.views.subscription.create_payment"
            ) as create_global_payment,
        ):
            response = self.client.post(
                reverse("clubs:my_plan_payment_process"),
                {
                    "id": str(self.plan.id),
                    "offer_accepted": "on",
                    "club_offer_accepted": "on",
                },
                secure=True,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://pay.example/club-plan")
        create_global_payment.assert_not_called()
        create_club_payment.assert_called_once()
        call_kwargs = create_club_payment.call_args.kwargs
        self.assertEqual(call_kwargs["shop_id"], "club-shop-001")
        self.assertEqual(call_kwargs["secret_key"], "club-secret-key")
        self.assertEqual(call_kwargs["amount"], "1200.00")
        self.assertEqual(call_kwargs["metadata"]["payment_type"], "club_plan")
        self.assertEqual(call_kwargs["metadata"]["club_id"], self.club.id)
        self.assertEqual(call_kwargs["metadata"]["club_slug"], self.club.slug)
        self.assertEqual(call_kwargs["metadata"]["club_plan_id"], self.plan.id)
        session = self.client.session
        self.assertEqual(
            session["club_plan_payment_pending"]["club_slug"], self.club.slug
        )
        self.assertEqual(session["club_plan_payment_pending"]["amount"], "1200.00")

    def test_club_plan_payment_process_restores_balance_on_requests_error(self) -> None:
        """При сбое HTTP-клиента резерв баланса снимается (регрессия SSL/необработанное исключение)."""
        self.member.balance = Decimal("500.00")
        self.member.save(update_fields=["balance"])
        self.plan.monthly_fee = Decimal("2000.00")
        self.plan.save(update_fields=["monthly_fee"])

        with (
            patch(
                "apps.clubs.views.subscription.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.views.subscription.create_payment_with_credentials",
                side_effect=requests.exceptions.SSLError("SSL EOF"),
            ),
        ):
            response = self.client.post(
                reverse("clubs:my_plan_payment_process"),
                {
                    "id": str(self.plan.id),
                    "offer_accepted": "on",
                    "club_offer_accepted": "on",
                },
                secure=True,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("clubs:my_plan_payment_preview", kwargs={"plan_id": self.plan.id}),
        )
        self.member.refresh_from_db()
        self.assertEqual(self.member.balance, Decimal("500.00"))
        self.assertFalse(
            ClubMemberBalanceTransaction.objects.filter(
                member=self.member,
                status=ClubMemberBalanceTransaction.Status.PENDING,
            ).exists()
        )

    def test_club_plan_payment_return_uses_club_credentials(self) -> None:
        with (
            patch(
                "apps.clubs.views.subscription.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.views.subscription.create_payment_with_credentials",
                return_value=("club-plan-pay-002", "https://pay.example/club-plan"),
            ),
            patch("apps.clubs.views.subscription.create_payment"),
        ):
            self.client.post(
                reverse("clubs:my_plan_payment_process"),
                {
                    "id": str(self.plan.id),
                    "offer_accepted": "on",
                    "club_offer_accepted": "on",
                    "enable_autopay": "on",
                },
                secure=True,
            )

        with (
            patch(
                "apps.clubs.views.subscription.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.views.subscription.get_payment_status_with_credentials",
                return_value="succeeded",
            ) as get_club_status,
            patch(
                "apps.clubs.views.subscription.get_payment_details_with_credentials",
                return_value={
                    "payment_method": {
                        "id": "club-plan-method-1",
                        "card": {
                            "last4": "4488",
                            "expiry_month": "12",
                            "expiry_year": "2030",
                            "card_type": "Mastercard",
                        },
                    }
                },
            ),
            patch(
                "apps.clubs.views.subscription.create_payment"
            ) as create_global_payment,
        ):
            response = self.client.get(
                reverse("clubs:my_plan_payment_return"),
                secure=True,
            )

        self.assertRedirects(
            response,
            reverse("clubs:my_plan"),
            fetch_redirect_response=False,
        )
        create_global_payment.assert_not_called()
        get_club_status.assert_called_once_with(
            "club-plan-pay-002",
            "club-shop-001",
            "club-secret-key",
        )
        payment_record = PaymentRecord.objects.get(
            yookassa_payment_id="club-plan-pay-002"
        )
        self.assertEqual(
            payment_record.payment_type, PaymentRecord.PaymentType.CLUB_PLAN
        )
        self.assertEqual(str(payment_record.metadata["club_id"]), str(self.club.id))
        self.assertEqual(payment_record.metadata["club_slug"], self.club.slug)
        saved_method = SavedPaymentMethod.objects.get(
            payment_method_id="club-plan-method-1"
        )
        self.assertEqual(saved_method.club, self.club)
        self.assertTrue(saved_method.is_default_for_club_plans)
        self.assertFalse(saved_method.is_default_for_subscriptions)
        self.assertTrue(
            ClubMemberPlan.objects.filter(
                club_member=self.member,
                plan=self.plan,
                status="active",
            ).exists()
        )

    def test_club_fee_payment_process_requires_offer_acceptance(self) -> None:
        with patch(
            "apps.clubs.views.payments.create_payment_with_credentials"
        ) as create_club_payment:
            response = self.client.post(
                reverse("clubs:my_fees_pay"),
                {},
                secure=True,
            )

        self.assertRedirects(
            response,
            reverse("clubs:my_fee_payment_preview"),
            fetch_redirect_response=False,
        )
        create_club_payment.assert_not_called()

    def test_club_fee_payment_process_uses_club_credentials_with_partial_balance(
        self,
    ) -> None:
        self.member.balance = Decimal("100.00")
        self.member.save(update_fields=["balance"])

        with (
            patch(
                "apps.clubs.views.payments.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.views.payments.create_payment_with_credentials",
                return_value=("club-fee-pay-001", "https://pay.example/club-fee"),
            ) as create_club_payment,
        ):
            response = self.client.post(
                reverse("clubs:my_fees_pay"),
                {
                    "offer_accepted": "on",
                    "club_offer_accepted": "on",
                },
                secure=True,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://pay.example/club-fee")
        create_club_payment.assert_called_once()
        call_kwargs = create_club_payment.call_args.kwargs
        self.assertEqual(call_kwargs["shop_id"], "club-shop-001")
        self.assertEqual(call_kwargs["secret_key"], "club-secret-key")
        self.assertEqual(call_kwargs["amount"], "200.00")
        self.assertEqual(call_kwargs["metadata"]["payment_type"], "club_fee")
        self.assertEqual(call_kwargs["metadata"]["club_id"], str(self.club.id))
        self.assertEqual(
            call_kwargs["metadata"]["fee_id"], str(self.payment_settings.id)
        )
        self.member.refresh_from_db()
        self.assertEqual(self.member.balance, Decimal("0.00"))
        balance_tx = ClubMemberBalanceTransaction.objects.get(member=self.member)
        self.assertEqual(
            balance_tx.status,
            ClubMemberBalanceTransaction.Status.PENDING,
        )
        self.assertEqual(balance_tx.amount, Decimal("100.00"))
        session = self.client.session
        self.assertEqual(
            session["club_fee_payment_pending"]["balance_amount"], "100.00"
        )
        self.assertEqual(session["club_fee_payment_pending"]["total_amount"], "300.00")

    def test_club_fee_payment_process_does_not_reserve_balance_when_already_paid(
        self,
    ) -> None:
        self.member.balance = Decimal("100.00")
        self.member.save(update_fields=["balance"])
        ClubFeePayment.objects.create(
            club=self.club,
            member=self.member,
            fee=self.payment_settings,
            amount=Decimal("300.00"),
            period_label=get_current_period_label(self.payment_settings),
            paid_at=timezone.now(),
            method=FeePaymentMethod.ONLINE,
            payment_ref="existing-fee-payment",
        )

        response = self.client.post(
            reverse("clubs:my_fees_pay"),
            {
                "offer_accepted": "on",
                "club_offer_accepted": "on",
            },
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("clubs:my_plan"),
            fetch_redirect_response=False,
        )
        self.member.refresh_from_db()
        self.assertEqual(self.member.balance, Decimal("100.00"))
        self.assertFalse(
            ClubMemberBalanceTransaction.objects.filter(
                member=self.member,
                source=ClubMemberBalanceTransaction.Source.CLUB_FEE_PAYMENT,
            ).exists()
        )
        self.assertFalse(ClubFeePaymentPending.objects.exists())

    def test_club_fee_payment_return_uses_club_credentials(self) -> None:
        with (
            patch(
                "apps.clubs.views.payments.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.views.payments.create_payment_with_credentials",
                return_value=("club-fee-pay-002", "https://pay.example/club-fee"),
            ),
        ):
            self.client.post(
                reverse("clubs:my_fees_pay"),
                {
                    "offer_accepted": "on",
                    "club_offer_accepted": "on",
                    "enable_autopay": "on",
                },
                secure=True,
            )

        with (
            patch(
                "apps.clubs.views.payments.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.views.payments.get_payment_status_with_credentials",
                return_value="succeeded",
            ) as get_club_status,
            patch(
                "apps.clubs.views.payments.get_payment_details_with_credentials",
                return_value={
                    "payment_method": {
                        "id": "club-fee-method-1",
                        "saved": True,
                        "card": {
                            "last4": "7788",
                            "expiry_month": "11",
                            "expiry_year": "2031",
                            "card_type": "Mir",
                        },
                    }
                },
            ) as get_club_details,
        ):
            response = self.client.get(
                reverse("clubs:my_fees_return"),
                secure=True,
            )

        self.assertRedirects(
            response,
            reverse("clubs:my_plan"),
            fetch_redirect_response=False,
        )
        get_club_status.assert_called_once_with(
            "club-fee-pay-002",
            "club-shop-001",
            "club-secret-key",
        )
        get_club_details.assert_called_once()
        self.assertTrue(
            ClubFeePayment.objects.filter(payment_ref="club-fee-pay-002").exists()
        )
        payment_record = PaymentRecord.objects.get(
            yookassa_payment_id="club-fee-pay-002"
        )
        self.assertEqual(
            payment_record.payment_type, PaymentRecord.PaymentType.CLUB_FEE
        )
        self.assertEqual(str(payment_record.metadata["club_id"]), str(self.club.id))
        self.assertEqual(payment_record.metadata["club_slug"], self.club.slug)
        saved_method = SavedPaymentMethod.objects.get(
            payment_method_id="club-fee-method-1"
        )
        self.assertEqual(saved_method.club, self.club)
        self.assertTrue(saved_method.is_default_for_club_fees)

    def test_my_payments_excludes_global_subscription_records(self) -> None:
        PaymentRecord.objects.create(
            user=self.user,
            payment_type=PaymentRecord.PaymentType.CLUB_PLAN,
            item_id=str(self.plan.id),
            item_label=f"{self.club.name}: {self.plan.name}",
            amount=Decimal("1200.00"),
            status="succeeded",
            yookassa_payment_id="club-plan-history-1",
            metadata={"club_id": self.club.id, "club_slug": self.club.slug},
        )
        PaymentRecord.objects.create(
            user=self.user,
            payment_type=PaymentRecord.PaymentType.SUBSCRIPTION,
            item_id="999",
            item_label="Глобальная подписка PRO",
            amount=Decimal("5000.00"),
            status="succeeded",
            yookassa_payment_id="global-subscription-1",
            metadata={},
        )

        response = self.client.get(reverse("clubs:my_payments"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.plan.name)
        self.assertNotContains(response, "Глобальная подписка PRO")

    def test_club_cashbox_history_excludes_global_and_foreign_records(self) -> None:
        other_club = Club.objects.create(
            name="Динамо",
            slug="dinamo-billing",
            city="Москва",
            address="ул. Центральная, 2",
            email="dinamo@test.local",
            admin_name="Администратор Динамо",
        )
        PaymentRecord.objects.create(
            user=self.user,
            payment_type=PaymentRecord.PaymentType.CLUB_PLAN,
            item_id=str(self.plan.id),
            item_label=f"{self.club.name}: {self.plan.name}",
            amount=Decimal("1200.00"),
            status="succeeded",
            yookassa_payment_id="cashbox-club-plan-1",
            metadata={"club_id": self.club.id, "club_slug": self.club.slug},
        )
        PaymentRecord.objects.create(
            user=self.user,
            payment_type=PaymentRecord.PaymentType.CLUB_FEE,
            item_id=str(self.payment_settings.id),
            item_label="Членский взнос клуба",
            amount=Decimal("300.00"),
            status="succeeded",
            yookassa_payment_id="cashbox-club-fee-1",
            metadata={"club_id": self.club.id, "club_slug": self.club.slug},
        )
        PaymentRecord.objects.create(
            user=self.user,
            payment_type=PaymentRecord.PaymentType.SUBSCRIPTION,
            item_id="999",
            item_label="Глобальная подписка PRO",
            amount=Decimal("5000.00"),
            status="succeeded",
            yookassa_payment_id="cashbox-global-subscription-1",
            metadata={},
        )
        PaymentRecord.objects.create(
            user=self.user,
            payment_type=PaymentRecord.PaymentType.CLUB_PLAN,
            item_id="777",
            item_label=f"{other_club.name}: Премиум",
            amount=Decimal("1500.00"),
            status="succeeded",
            yookassa_payment_id="cashbox-other-club-plan-1",
            metadata={"club_id": other_club.id, "club_slug": other_club.slug},
        )

        response = self.client.get(
            reverse("clubs:club_cashbox_history", kwargs={"slug": self.club.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"{self.club.name}: {self.plan.name}")
        self.assertContains(response, "Членский взнос клуба")
        self.assertNotContains(response, "Глобальная подписка PRO")
        self.assertNotContains(response, f"{other_club.name}: Премиум")

    def test_club_cashbox_shows_yookassa_and_balance_split_for_mixed_payment(
        self,
    ) -> None:
        """В кассе клуба видна разбивка: ЮKassa и внутренний баланс игрока."""
        PaymentRecord.objects.create(
            user=self.user,
            payment_type=PaymentRecord.PaymentType.CLUB_PLAN,
            item_id=str(self.plan.id),
            item_label=f"{self.club.name}: {self.plan.name}",
            amount=Decimal("2000.00"),
            status="succeeded",
            yookassa_payment_id="split-cashbox-1",
            metadata={
                "club_id": self.club.id,
                "club_slug": self.club.slug,
                "balance_amount": "500.00",
                "external_amount": "1500.00",
                "total_amount": "2000.00",
            },
            paid_at=timezone.now(),
        )
        response = self.client.get(
            reverse("clubs:club_cashbox_history", kwargs={"slug": self.club.slug}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Баланс + ЮKassa")
        self.assertContains(response, "ЮKassa (поступило на счёт)")
        self.assertContains(response, "1500.00")
        self.assertContains(response, "500.00")

    def test_recurring_club_plan_command_uses_club_credentials_and_plan_metadata(
        self,
    ) -> None:
        member_plan = purchase_member_plan(
            self.member,
            self.plan,
            assigned_by=self.user,
            auto_renew=True,
        )
        member_plan.ended_at = timezone.now()
        member_plan.auto_renew = True
        member_plan.save(update_fields=["ended_at", "auto_renew"])
        SavedPaymentMethod.objects.create(
            user=self.user,
            club=self.club,
            payment_method_id="club-plan-recurring-method-1",
            card_last4="4488",
            card_network="Mastercard",
            is_active=True,
            is_default_for_subscriptions=False,
            is_default_for_club_plans=True,
        )

        with (
            patch(
                "apps.clubs.management.commands.run_recurring_club_plan_payments.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.management.commands.run_recurring_club_plan_payments.create_recurring_payment_with_credentials",
                return_value=("club-plan-recurring-pay-1", "pending"),
            ) as create_club_payment,
        ):
            call_command(
                "run_recurring_club_plan_payments",
                stdout=StringIO(),
            )

        create_club_payment.assert_called_once()
        call_kwargs = create_club_payment.call_args.kwargs
        self.assertEqual(call_kwargs["shop_id"], "club-shop-001")
        self.assertEqual(call_kwargs["secret_key"], "club-secret-key")
        self.assertEqual(call_kwargs["amount"], "1200.00")
        self.assertEqual(
            call_kwargs["payment_method_id"],
            "club-plan-recurring-method-1",
        )
        self.assertEqual(
            call_kwargs["metadata"]["payment_type"],
            PaymentRecord.PaymentType.CLUB_PLAN,
        )
        self.assertEqual(call_kwargs["metadata"]["club_id"], self.club.id)
        self.assertEqual(call_kwargs["metadata"]["club_plan_id"], self.plan.id)
        self.assertEqual(call_kwargs["metadata"]["enable_autopay"], "1")
        self.assertEqual(call_kwargs["metadata"]["autopay"], "1")
        payment_record = PaymentRecord.objects.get(
            yookassa_payment_id="club-plan-recurring-pay-1"
        )
        self.assertEqual(
            payment_record.payment_type, PaymentRecord.PaymentType.CLUB_PLAN
        )
        self.assertEqual(payment_record.status, "pending")
        self.assertTrue(payment_record.is_recurring)
        self.assertTrue(payment_record.autopay_enabled)
        self.assertEqual(str(payment_record.metadata["club_id"]), str(self.club.id))

    def test_recurring_club_fee_command_uses_club_credentials_and_records_payment(
        self,
    ) -> None:
        SavedPaymentMethod.objects.create(
            user=self.user,
            club=self.club,
            payment_method_id="club-fee-recurring-method-1",
            card_last4="2211",
            card_network="Mir",
            is_active=True,
            is_default_for_subscriptions=False,
            is_default_for_club_fees=True,
        )
        period_label = get_current_period_label(self.payment_settings)

        with (
            patch(
                "apps.clubs.management.commands.run_recurring_club_fee_payments.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.management.commands.run_recurring_club_fee_payments.create_recurring_payment_with_credentials",
                return_value=("club-fee-recurring-pay-1", "succeeded"),
            ) as create_club_payment,
        ):
            call_command(
                "run_recurring_club_fee_payments",
                stdout=StringIO(),
            )

        create_club_payment.assert_called_once()
        call_kwargs = create_club_payment.call_args.kwargs
        self.assertEqual(call_kwargs["shop_id"], "club-shop-001")
        self.assertEqual(call_kwargs["secret_key"], "club-secret-key")
        self.assertEqual(call_kwargs["amount"], "300.00")
        self.assertEqual(
            call_kwargs["payment_method_id"],
            "club-fee-recurring-method-1",
        )
        self.assertEqual(
            call_kwargs["metadata"]["payment_type"],
            PaymentRecord.PaymentType.CLUB_FEE,
        )
        self.assertEqual(call_kwargs["metadata"]["club_id"], self.club.id)
        self.assertEqual(call_kwargs["metadata"]["fee_id"], self.payment_settings.id)
        self.assertEqual(call_kwargs["metadata"]["member_id"], self.member.id)
        self.assertEqual(call_kwargs["metadata"]["period_label"], period_label)
        self.assertEqual(call_kwargs["metadata"]["autopay"], "1")
        self.assertTrue(
            ClubFeePayment.objects.filter(
                payment_ref="club-fee-recurring-pay-1",
                period_label=period_label,
            ).exists()
        )
        payment_record = PaymentRecord.objects.get(
            yookassa_payment_id="club-fee-recurring-pay-1"
        )
        self.assertEqual(
            payment_record.payment_type, PaymentRecord.PaymentType.CLUB_FEE
        )
        self.assertEqual(payment_record.status, "succeeded")
        self.assertTrue(payment_record.is_recurring)
        self.assertTrue(payment_record.autopay_enabled)
        self.assertEqual(str(payment_record.metadata["club_id"]), str(self.club.id))
        self.assertEqual(payment_record.metadata["period_label"], period_label)

    def test_club_payment_webhook_updates_recurring_club_plan_payment(self) -> None:
        payment_record = PaymentRecord.objects.create(
            user=self.user,
            payment_type=PaymentRecord.PaymentType.CLUB_PLAN,
            item_id=str(self.plan.id),
            item_label=f"{self.club.name}: {self.plan.name}",
            amount=Decimal("1200.00"),
            status="pending",
            yookassa_payment_id="club-plan-webhook-1",
            is_recurring=True,
            autopay_enabled=True,
            metadata={
                "club_id": self.club.id,
                "club_member_plan_id": "42",
                "balance_amount": "200.00",
            },
        )
        payload_metadata = {
            "payment_type": PaymentRecord.PaymentType.CLUB_PLAN,
            "club_id": self.club.id,
            "club_plan_id": self.plan.id,
            "user_id": self.user.id,
            "autopay": "1",
        }

        with (
            patch(
                "apps.clubs.views.payments.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.views.payments.get_payment_details_with_credentials",
                return_value={
                    "id": "club-plan-webhook-1",
                    "status": "succeeded",
                    "metadata": payload_metadata,
                },
            ) as get_club_details,
        ):
            response = self.client.post(
                reverse("clubs:club_payment_webhook"),
                data=json.dumps(
                    {
                        "event": "payment.succeeded",
                        "object": {
                            "id": "club-plan-webhook-1",
                            "status": "succeeded",
                            "metadata": payload_metadata,
                        },
                    }
                ),
                content_type="application/json",
                secure=True,
            )

        self.assertEqual(response.status_code, 200)
        get_club_details.assert_called_once_with(
            "club-plan-webhook-1",
            "club-shop-001",
            "club-secret-key",
        )
        payment_record.refresh_from_db()
        self.assertEqual(payment_record.status, "succeeded")
        self.assertTrue(payment_record.is_recurring)
        self.assertTrue(payment_record.autopay_enabled)
        self.assertEqual(str(payment_record.metadata["club_id"]), str(self.club.id))
        self.assertEqual(payment_record.metadata["club_slug"], self.club.slug)
        self.assertEqual(payment_record.metadata["balance_amount"], "200.00")
        active_plan = ClubMemberPlan.objects.get(
            club_member=self.member,
            status="active",
        )
        self.assertEqual(active_plan.plan, self.plan)
        self.assertTrue(active_plan.auto_renew)

    def test_club_payment_webhook_updates_recurring_club_fee_payment(self) -> None:
        period_label = get_current_period_label(self.payment_settings)
        payment_record = PaymentRecord.objects.create(
            user=self.user,
            payment_type=PaymentRecord.PaymentType.CLUB_FEE,
            item_id=str(self.payment_settings.id),
            item_label="Членский взнос клуба",
            amount=Decimal("300.00"),
            status="pending",
            yookassa_payment_id="club-fee-webhook-1",
            is_recurring=True,
            autopay_enabled=True,
            metadata={
                "club_id": self.club.id,
                "balance_amount": "50.00",
                "external_amount": "250.00",
            },
        )
        payload_metadata = {
            "payment_type": PaymentRecord.PaymentType.CLUB_FEE,
            "club_id": self.club.id,
            "fee_id": self.payment_settings.id,
            "member_id": self.member.id,
            "period_label": period_label,
            "autopay": "1",
        }

        with (
            patch(
                "apps.clubs.views.payments.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.views.payments.get_payment_details_with_credentials",
                return_value={
                    "id": "club-fee-webhook-1",
                    "status": "succeeded",
                    "amount": {"value": "300.00"},
                    "metadata": payload_metadata,
                },
            ) as get_club_details,
            patch(
                "apps.clubs.views.payments.send_fee_paid_notification"
            ) as notify_paid,
        ):
            response = self.client.post(
                reverse("clubs:club_payment_webhook"),
                data=json.dumps(
                    {
                        "event": "payment.succeeded",
                        "object": {
                            "id": "club-fee-webhook-1",
                            "status": "succeeded",
                            "metadata": payload_metadata,
                        },
                    }
                ),
                content_type="application/json",
                secure=True,
            )

        self.assertEqual(response.status_code, 200)
        get_club_details.assert_called_once_with(
            "club-fee-webhook-1",
            "club-shop-001",
            "club-secret-key",
        )
        payment_record.refresh_from_db()
        self.assertEqual(payment_record.status, "succeeded")
        self.assertTrue(payment_record.is_recurring)
        self.assertTrue(payment_record.autopay_enabled)
        self.assertEqual(payment_record.metadata["club_slug"], self.club.slug)
        self.assertEqual(payment_record.metadata["balance_amount"], "50.00")
        self.assertTrue(
            ClubFeePayment.objects.filter(
                payment_ref="club-fee-webhook-1",
                period_label=period_label,
            ).exists()
        )
        notify_paid.assert_called_once()
        cashbox_response = self.client.get(
            reverse("clubs:club_cashbox_history", kwargs={"slug": self.club.slug}),
            secure=True,
        )
        self.assertEqual(cashbox_response.status_code, 200)
        self.assertContains(cashbox_response, "Членский взнос клуба")

    def test_club_payment_webhook_ignores_unknown_payment_type(self) -> None:
        period_label = get_current_period_label(self.payment_settings)
        payload_metadata = {
            "payment_type": "subscription",
            "club_id": self.club.id,
            "fee_id": self.payment_settings.id,
            "member_id": self.member.id,
            "period_label": period_label,
        }

        with (
            patch(
                "apps.clubs.views.payments.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.clubs.views.payments.get_payment_details_with_credentials",
                return_value={
                    "id": "foreign-webhook-1",
                    "status": "succeeded",
                    "amount": {"value": "300.00"},
                    "metadata": payload_metadata,
                },
            ),
        ):
            response = self.client.post(
                reverse("clubs:club_payment_webhook"),
                data=json.dumps(
                    {
                        "event": "payment.succeeded",
                        "object": {
                            "id": "foreign-webhook-1",
                            "status": "succeeded",
                            "metadata": payload_metadata,
                        },
                    }
                ),
                content_type="application/json",
                secure=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            PaymentRecord.objects.filter(
                yookassa_payment_id="foreign-webhook-1"
            ).exists()
        )
        self.assertFalse(
            ClubFeePayment.objects.filter(payment_ref="foreign-webhook-1").exists()
        )
        self.assertFalse(
            ClubMemberPlan.objects.filter(
                club_member=self.member,
                status="active",
            ).exists()
        )
