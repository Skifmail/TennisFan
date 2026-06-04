"""E2E: оплата регистрации на клубный турнир."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    Club,
    ClubMember,
    ClubMemberRole,
    ClubMembershipFee,
    ClubMemberStatus,
)
from apps.payments.models import PaymentRecord
from apps.subscriptions.models import SubscriptionTier, UserSubscription
from apps.tournaments.models import (
    Tournament,
    TournamentFormat,
    TournamentGender,
    TournamentStatus,
)
from apps.users.models import Player, User


@override_settings(
    STORAGES={
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class ClubTournamentPaymentFlowTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="club-payments@test.local",
            password="testpass123",
            first_name="Анна",
            last_name="Шатайло",
        )
        self.player = Player.objects.create(
            user=self.user,
            skill_level="amateur",
            birth_date=date(1992, 1, 1),
        )
        self.club = Club.objects.create(
            name="Спартак",
            slug="spartak-club-payments",
            city="Москва",
            address="ул. Спортивная, 1",
            email="club-payments@test.local",
            admin_name="Администратор клуба",
        )
        self.member = ClubMember.objects.create(
            club=self.club,
            user=self.user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        ClubMembershipFee.objects.create(
            club=self.club,
            amount=300,
            currency="RUB",
            period="monthly",
            period_start_day=1,
            payment_provider=ClubMembershipFee.PaymentProvider.YOOKASSA,
            payment_shop_id="club-shop-001",
            payment_api_key="club-secret-key",
            is_active=False,
        )
        self.tournament = Tournament.objects.create(
            name="Женский тестовый",
            slug="club-payment-preview",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            gender=TournamentGender.OPEN,
            is_one_day=True,
            entry_fee=800,
        )
        tier = SubscriptionTier.objects.create(
            name="diamond",
            display_name="ДИАМАНТ",
            price=5000,
            fancoin_per_purchase=20,
            duration_days=30,
            one_day_tournament_discount=25,
            is_visible=True,
        )
        UserSubscription.objects.create(
            user=self.user,
            tier=tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
            is_active=True,
            fancoin_balance=20,
        )
        self.client.force_login(self.user)

    def test_club_tournament_preview_uses_club_context_without_global_discount(
        self,
    ) -> None:
        response = self.client.get(
            reverse("payment_preview"),
            {"type": "tournament", "id": str(self.tournament.id)},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["amount"], Decimal("800"))
        self.assertEqual(response.context["external_amount_due"], Decimal("800"))
        self.assertTrue(response.context["is_club_panel"])
        self.assertEqual(response.context["club"], self.club)
        details = dict(response.context["details"])
        self.assertEqual(details["Клуб"], self.club.name)
        self.assertEqual(details["Получатель"], f"{self.club.name} · YooKassa клуба")
        self.assertEqual(details["Скидка"], "Нет")
        self.assertContains(response, "body--club-panel")
        self.assertContains(response, "Личный кабинет")
        self.assertContains(response, self.club.name)

    def test_club_tournament_process_uses_club_yookassa_credentials(self) -> None:
        with (
            patch(
                "apps.payments.views.create_payment_with_credentials",
                return_value=("club-pay-001", "https://pay.example/club"),
            ) as create_club_payment,
            patch("apps.payments.views.create_payment") as create_global_payment,
        ):
            response = self.client.post(
                reverse("payment_process"),
                {
                    "type": "tournament",
                    "id": str(self.tournament.id),
                    "amount": "800.00",
                    "offer_accepted": "on",
                },
                secure=True,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://pay.example/club")
        create_global_payment.assert_not_called()
        create_club_payment.assert_called_once()
        call_kwargs = create_club_payment.call_args.kwargs
        self.assertEqual(call_kwargs["shop_id"], "club-shop-001")
        self.assertEqual(call_kwargs["secret_key"], "club-secret-key")
        self.assertEqual(call_kwargs["amount"], "800.00")
        self.assertIn(self.club.name, call_kwargs["description"])
        self.assertEqual(call_kwargs["metadata"]["club_id"], str(self.club.id))
        self.assertEqual(call_kwargs["metadata"]["club_slug"], self.club.slug)
        session = self.client.session
        self.assertEqual(session["yookassa_pending"]["club_id"], str(self.club.id))
        self.assertEqual(session["yookassa_pending"]["club_slug"], self.club.slug)

    def test_club_tournament_return_checks_status_via_club_credentials(self) -> None:
        payment_id = "club-pay-002"
        with (
            patch(
                "apps.payments.views.create_payment_with_credentials",
                return_value=(payment_id, "https://pay.example/club"),
            ),
            patch("apps.payments.views.create_payment"),
        ):
            self.client.post(
                reverse("payment_process"),
                {
                    "type": "tournament",
                    "id": str(self.tournament.id),
                    "amount": "800.00",
                    "offer_accepted": "on",
                },
                secure=True,
            )

        with (
            patch(
                "apps.clubs.payment_utils.decrypt_secret",
                return_value="club-secret-key",
            ),
            patch(
                "apps.payments.yookassa_client.get_payment_status_with_credentials",
                return_value="succeeded",
            ) as get_club_status,
            patch(
                "apps.payments.yookassa_client.get_payment_status"
            ) as get_global_status,
        ):
            response = self.client.get(reverse("payment_return"), secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("payment_success"), response["Location"])
        get_global_status.assert_not_called()
        get_club_status.assert_called_once()
        record = PaymentRecord.objects.get(yookassa_payment_id="club-pay-002")
        self.assertEqual(record.payment_type, PaymentRecord.PaymentType.TOURNAMENT)
        self.assertEqual(record.item_label, f"{self.club.name}: {self.tournament.name}")
        self.assertEqual(str(record.metadata.get("club_id")), str(self.club.id))
        self.assertEqual(str(record.metadata.get("club_slug")), self.club.slug)
