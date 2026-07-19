"""Интеграционные тесты: постоплата турниров и FANcoin."""

from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.subscriptions.fancoin import TOURNAMENT_REGISTRATION_COST
from apps.subscriptions.models import (
    FancoinTransaction,
    SubscriptionTier,
    UserSubscription,
)
from apps.tournaments.models import (
    Tournament,
    TournamentPostpaymentCallLog,
    TournamentPostpaymentInvoice,
    TournamentRegistrationCoverage,
)
from apps.tournaments.postpayment import (
    _SUBSCRIPTION_SLOT_COVERAGE,
    _payment_url,
    admin_confirm_postpayment_participation,
    build_participant_payment_statuses,
    finalize_postpayment_window,
    get_pending_postpayment_users,
    mark_postpayment_call,
    mark_registration_covered,
    open_postpayment_window,
    phone_to_tel_href,
    settle_postpayment_with_available_fancoin,
    sync_postpayment_invoices_deadline,
    tournament_needs_fancoin_settlement,
    try_cover_registration_with_fancoin,
    try_settle_postpayment_for_user,
)
from apps.users.models import Player, SkillLevel, User


class TournamentPostpaymentServiceTestCase(TestCase):
    """Тесты сервиса постоплаты турнира."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(email="postpay@test.local", password="x")
        self.user2 = User.objects.create_user(email="postpay2@test.local", password="x")
        self.player = Player.objects.create(
            user=self.user, skill_level=SkillLevel.AMATEUR
        )
        self.player2 = Player.objects.create(
            user=self.user2, skill_level=SkillLevel.AMATEUR
        )
        self.tournament = Tournament.objects.create(
            name="Postpayment test",
            slug="postpayment-test",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            entry_fee=1000,
            allow_postpayment=True,
            is_one_day=False,
            max_participants=32,
        )
        self.tournament.allowed_categories.create(category=SkillLevel.AMATEUR)
        self.tournament.participants.add(self.player, self.player2)

    def test_get_pending_postpayment_users_returns_uncovered_players(self) -> None:
        pending_users = get_pending_postpayment_users(self.tournament)
        self.assertEqual({u.id for u in pending_users}, {self.user.id, self.user2.id})

    def test_mark_registration_covered_excludes_user_from_pending(self) -> None:
        mark_registration_covered(
            self.tournament,
            self.user,
            _SUBSCRIPTION_SLOT_COVERAGE,
        )
        pending_users = get_pending_postpayment_users(self.tournament)
        self.assertEqual({u.id for u in pending_users}, {self.user2.id})

    def test_paid_invoice_excludes_user_from_pending(self) -> None:
        TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.user2,
            amount=1000,
            due_at=timezone.now() + timedelta(hours=12),
            status=TournamentPostpaymentInvoice.Status.PAID,
        )
        pending_users = get_pending_postpayment_users(self.tournament)
        self.assertEqual({u.id for u in pending_users}, {self.user.id})

    def _create_subscription_with_fancoin(self, user: User, balance: int) -> None:
        tier = SubscriptionTier.objects.create(
            name=f"tier-{user.pk}",
            display_name="Test",
            fancoin_per_purchase=15,
            duration_days=30,
            is_visible=True,
            is_unlimited=False,
        )
        UserSubscription.objects.create(
            user=user,
            tier=tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
            fancoin_balance=balance,
            is_active=True,
        )

    def test_try_cover_registration_with_fancoin(self) -> None:
        self._create_subscription_with_fancoin(self.user, TOURNAMENT_REGISTRATION_COST)
        covered = try_cover_registration_with_fancoin(self.tournament, self.user)
        self.assertTrue(covered)
        self.assertTrue(
            self.tournament.registration_coverages.filter(user=self.user).exists()
        )
        pending_users = get_pending_postpayment_users(self.tournament)
        self.assertEqual({u.id for u in pending_users}, {self.user2.id})

    def test_open_postpayment_window_skips_payment_notification_when_fancoin_available(
        self,
    ) -> None:

        self._create_subscription_with_fancoin(self.user, TOURNAMENT_REGISTRATION_COST)
        with (
            patch(
                "apps.tournaments.postpayment._send_postpayment_opened_notification"
            ) as payment_notify_mock,
            patch(
                "apps.tournaments.postpayment._send_fancoin_settled_notification"
            ) as fancoin_notify_mock,
        ):
            created, fancoin_settled = open_postpayment_window(self.tournament)
        self.assertEqual(created, 1)
        self.assertEqual(fancoin_settled, 1)
        payment_notify_mock.assert_called_once()
        fancoin_notify_mock.assert_called_once()
        self.tournament.refresh_from_db()
        self.assertIsNotNone(self.tournament.postpayment_window_started_at)
        self.assertFalse(
            TournamentPostpaymentInvoice.objects.filter(
                tournament=self.tournament,
                user=self.user,
            ).exists()
        )

    def test_settle_postpayment_with_available_fancoin_cancels_pending_invoice(
        self,
    ) -> None:
        self.tournament.postpayment_window_started_at = timezone.now()
        self.tournament.save(update_fields=["postpayment_window_started_at"])
        due_at = timezone.now() + timedelta(hours=12)
        TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.user,
            amount=1000,
            due_at=due_at,
            status=TournamentPostpaymentInvoice.Status.PENDING,
        )
        self._create_subscription_with_fancoin(self.user, TOURNAMENT_REGISTRATION_COST)
        settled = settle_postpayment_with_available_fancoin()
        self.assertEqual(settled, 1)
        invoice = TournamentPostpaymentInvoice.objects.get(
            tournament=self.tournament,
            user=self.user,
        )
        self.assertEqual(invoice.status, TournamentPostpaymentInvoice.Status.CANCELLED)
        self.assertTrue(
            self.tournament.registration_coverages.filter(user=self.user).exists()
        )
        sub = UserSubscription.objects.get(user=self.user)
        self.assertEqual(sub.fancoin_balance, 0)
        self.assertTrue(
            FancoinTransaction.objects.filter(
                user=self.user,
                reason=FancoinTransaction.Reason.TOURNAMENT_REGISTRATION,
                tournament=self.tournament,
            ).exists()
        )

    def test_settle_sends_notification_when_invoice_was_pending(self) -> None:

        self.tournament.postpayment_window_started_at = timezone.now()
        self.tournament.save(update_fields=["postpayment_window_started_at"])
        due_at = timezone.now() + timedelta(hours=12)
        TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.user,
            amount=1000,
            due_at=due_at,
            status=TournamentPostpaymentInvoice.Status.PENDING,
        )
        self._create_subscription_with_fancoin(self.user, TOURNAMENT_REGISTRATION_COST)
        with (
            patch("apps.tournaments.postpayment.send_to_user_by_user") as tg_mock,
            patch(
                "apps.core.email_service.send_tournament_entry_fancoin_confirmed_email",
                return_value=True,
            ) as email_mock,
        ):
            settle_postpayment_with_available_fancoin()
        tg_mock.assert_called_once()
        email_mock.assert_called_once()
        self.assertTrue(email_mock.call_args.kwargs["had_payment_request"])

    def test_try_settle_postpayment_for_user_after_subscription_purchase(self) -> None:
        """Покупка подписки с FT сразу закрывает pending-инвойс постоплаты."""
        self.tournament.postpayment_window_started_at = timezone.now()
        self.tournament.save(update_fields=["postpayment_window_started_at"])
        TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.user,
            amount=1000,
            due_at=timezone.now() + timedelta(hours=12),
            status=TournamentPostpaymentInvoice.Status.PENDING,
        )
        self._create_subscription_with_fancoin(self.user, 0)
        sub = UserSubscription.objects.get(user=self.user)
        sub.add_fancoin(TOURNAMENT_REGISTRATION_COST)
        invoice = TournamentPostpaymentInvoice.objects.get(
            tournament=self.tournament,
            user=self.user,
        )
        self.assertEqual(invoice.status, TournamentPostpaymentInvoice.Status.CANCELLED)
        self.assertTrue(
            self.tournament.registration_coverages.filter(user=self.user).exists()
        )

    def test_try_settle_postpayment_for_user_before_window_open(self) -> None:
        """FT списываются до открытия окна постоплаты, если баланс появился после регистрации."""
        self._create_subscription_with_fancoin(self.user, TOURNAMENT_REGISTRATION_COST)
        settled = try_settle_postpayment_for_user(self.user)
        self.assertEqual(settled, 1)
        self.assertTrue(
            self.tournament.registration_coverages.filter(user=self.user).exists()
        )
        pending_users = get_pending_postpayment_users(self.tournament)
        self.assertEqual({u.id for u in pending_users}, {self.user2.id})

    def test_settle_postpayment_with_available_fancoin_before_window_open(self) -> None:
        """Cron находит участников с FT до открытия окна постоплаты."""
        self._create_subscription_with_fancoin(self.user, TOURNAMENT_REGISTRATION_COST)
        settled = settle_postpayment_with_available_fancoin()
        self.assertEqual(settled, 1)
        self.assertTrue(
            self.tournament.registration_coverages.filter(user=self.user).exists()
        )

    def test_extending_deadline_hours_updates_invoice_due_at(self) -> None:
        """Продление окна постоплаты в турнире сдвигает due_at инвойсов."""
        started = timezone.now() - timedelta(hours=15)
        self.tournament.postpayment_window_started_at = started
        self.tournament.postpayment_deadline_hours = 12
        self.tournament.save(
            update_fields=[
                "postpayment_window_started_at",
                "postpayment_deadline_hours",
            ]
        )
        invoice = TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.user,
            amount=1000,
            due_at=started + timedelta(hours=12),
            status=TournamentPostpaymentInvoice.Status.PENDING,
        )
        self.tournament.postpayment_deadline_hours = 24
        self.tournament.save(update_fields=["postpayment_deadline_hours"])
        invoice.refresh_from_db()
        self.assertEqual(invoice.due_at, started + timedelta(hours=24))
        self.assertGreater(invoice.due_at, timezone.now())

    def test_sync_reopens_expired_invoice_when_window_extended(self) -> None:
        """При продлении окна EXPIRED-инвойс участника снова становится PENDING."""
        started = timezone.now() - timedelta(hours=15)
        self.tournament.postpayment_window_started_at = started
        self.tournament.postpayment_deadline_hours = 24
        self.tournament.save(
            update_fields=[
                "postpayment_window_started_at",
                "postpayment_deadline_hours",
            ]
        )
        invoice = TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.user,
            amount=1000,
            due_at=started + timedelta(hours=12),
            status=TournamentPostpaymentInvoice.Status.EXPIRED,
        )
        updated = sync_postpayment_invoices_deadline(self.tournament)
        self.assertEqual(updated, 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, TournamentPostpaymentInvoice.Status.PENDING)
        self.assertEqual(invoice.due_at, started + timedelta(hours=24))

    def test_payment_url_is_absolute(self) -> None:
        """Ссылка на оплату постоплаты должна быть абсолютной."""
        url = _payment_url(self.tournament, 42)
        self.assertTrue(url.startswith("http"))
        self.assertIn("invoice=42", url)
        self.assertIn(str(self.tournament.id), url)

    def test_phone_to_tel_href_normalizes_russian_numbers(self) -> None:
        """Телефон нормализуется в tel:+7… для ссылки звонка."""
        self.assertEqual(phone_to_tel_href("+7 (900) 123-45-67"), "tel:+79001234567")
        self.assertEqual(phone_to_tel_href("89001234567"), "tel:+79001234567")
        self.assertEqual(phone_to_tel_href(""), "")

    def test_mark_postpayment_call_and_status_row(self) -> None:
        """Отметка звонка сохраняется и попадает в статус участника."""
        self.user.phone = "+79001112233"
        self.user.save(update_fields=["phone"])
        log = mark_postpayment_call(self.tournament, self.user, called_by=self.user)
        self.assertIsInstance(log, TournamentPostpaymentCallLog)
        rows = {
            row.user_id: row
            for row in build_participant_payment_statuses(self.tournament)
        }
        self.assertEqual(rows[self.user.id].phone, "+79001112233")
        self.assertIsNotNone(rows[self.user.id].called_at)
        self.assertIsNone(rows[self.user2.id].called_at)

    def test_admin_confirm_postpayment_participation_cancels_invoice(self) -> None:
        """Ручное подтверждение админом покрывает участие и отменяет инвойс."""
        self.tournament.postpayment_window_started_at = timezone.now()
        self.tournament.save(update_fields=["postpayment_window_started_at"])
        TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.user,
            amount=500,
            due_at=timezone.now() + timedelta(hours=12),
            status=TournamentPostpaymentInvoice.Status.PENDING,
        )
        ok = admin_confirm_postpayment_participation(self.tournament, self.user)
        self.assertTrue(ok)
        coverage = TournamentRegistrationCoverage.objects.get(
            tournament=self.tournament,
            user=self.user,
        )
        self.assertEqual(
            coverage.coverage_type,
            TournamentRegistrationCoverage.CoverageType.ADMIN_GRANTED,
        )
        invoice = TournamentPostpaymentInvoice.objects.get(
            tournament=self.tournament,
            user=self.user,
        )
        self.assertEqual(invoice.status, TournamentPostpaymentInvoice.Status.CANCELLED)
        rows = {
            row.user_id: row
            for row in build_participant_payment_statuses(self.tournament)
        }
        self.assertEqual(rows[self.user.id].status, "Выдано администратором")
        self.assertEqual(rows[self.user.id].status_tone, "success")

    def test_finalize_postpayment_regenerates_when_flag_without_matches(self) -> None:

        self.tournament.bracket_generated = True
        self.tournament.postpayment_window_started_at = timezone.now()
        self.tournament.save(
            update_fields=["bracket_generated", "postpayment_window_started_at"]
        )
        mark_registration_covered(
            self.tournament,
            self.user,
            _SUBSCRIPTION_SLOT_COVERAGE,
        )
        mark_registration_covered(
            self.tournament,
            self.user2,
            _SUBSCRIPTION_SLOT_COVERAGE,
        )
        with patch(
            "apps.tournaments.postpayment._generate_after_postpayment",
            return_value=(True, "Сетка сформирована: 15 матчей"),
        ) as generate_mock:
            ok, msg = finalize_postpayment_window(self.tournament)
        self.assertTrue(ok)
        generate_mock.assert_called_once()
        self.assertIn("15 матчей", msg)

    def test_tournament_needs_fancoin_settlement_with_bracket_and_pending_invoice(
        self,
    ) -> None:
        self.tournament.bracket_generated = True
        self.tournament.postpayment_window_started_at = timezone.now()
        self.tournament.save(
            update_fields=["bracket_generated", "postpayment_window_started_at"]
        )
        TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.user,
            amount=1000,
            due_at=timezone.now() + timedelta(hours=12),
            status=TournamentPostpaymentInvoice.Status.PENDING,
        )
        self.assertTrue(tournament_needs_fancoin_settlement(self.tournament))

    def test_build_participant_payment_statuses(self) -> None:
        mark_registration_covered(
            self.tournament,
            self.user,
            _SUBSCRIPTION_SLOT_COVERAGE,
        )
        TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.user2,
            amount=1000,
            due_at=timezone.now() + timedelta(hours=12),
            status=TournamentPostpaymentInvoice.Status.PENDING,
        )
        rows = {
            row.user_id: row
            for row in build_participant_payment_statuses(self.tournament)
        }
        self.assertIn("FT", rows[self.user.id].status)
        self.assertEqual(rows[self.user.id].status_tone, "success")
        self.assertEqual(rows[self.user2.id].status, "Ожидает оплату (₽)")
        self.assertEqual(rows[self.user2.id].status_tone, "danger")
        self.assertIn("уведомление", rows[self.user2.id].details)
