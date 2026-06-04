"""Интеграционные тесты: клубные тарифы и подписки клуба."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    Club,
    ClubMember,
    ClubMemberRole,
    ClubMemberStatus,
    ClubPlan,
    ClubPlanSlotUsage,
    ClubPlayerPlan,
    ClubRegistrationLimitPeriod,
    ClubStatus,
    ClubSubscription,
    ClubSubscriptionPaymentPending,
    ClubSubscriptionPeriod,
    ClubSubscriptionStatus,
)
from apps.clubs.plan_services import (
    can_member_register_for_tournament,
    consume_member_tournament_limit,
    get_member_plan_limits,
    purchase_member_plan,
)
from apps.clubs.services import club_is_operational
from apps.tournaments.models import (
    Tournament,
    TournamentFormat,
    TournamentGender,
    TournamentStatus,
)
from apps.users.models import User


class ClubSubscriptionStatusSyncTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="club-owner@test.local",
            password="testpass123",
        )
        self.club = Club.objects.create(
            name="Клуб со статусом trial",
            slug="club-trial-sync",
            city="Москва",
            address="ул. Пушкина, 1",
            email="club@test.local",
            admin_name="Администратор клуба",
            status=ClubStatus.TRIAL,
            trial_ends_at=timezone.now() - timedelta(days=1),
        )
        ClubMember.objects.create(
            club=self.club,
            user=self.user,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )
        self.client.force_login(self.user)

    def test_subscription_return_activates_trial_club_after_success_payment(
        self,
    ) -> None:
        pending = ClubSubscriptionPaymentPending.objects.create(
            payment_id="club-sub-pay-001",
            club=self.club,
            plan=ClubPlan.BASIC,
            period="yearly",
            amount=Decimal("19900.00"),
        )

        with patch(
            "apps.payments.yookassa_client.get_payment_status",
            return_value="succeeded",
        ):
            response = self.client.get(
                reverse("clubs:subscription_return", kwargs={"slug": self.club.slug}),
                {"payment_id": pending.payment_id},
                secure=True,
            )

        self.assertRedirects(
            response,
            reverse("clubs:subscription", kwargs={"slug": self.club.slug}),
            fetch_redirect_response=False,
        )
        self.club.refresh_from_db()
        self.assertEqual(self.club.status, ClubStatus.ACTIVE)
        self.assertIsNone(self.club.trial_ends_at)
        self.assertTrue(
            ClubSubscription.objects.filter(
                club=self.club,
                plan=ClubPlan.BASIC,
                period=ClubSubscriptionPeriod.YEARLY,
                status=ClubSubscriptionStatus.ACTIVE,
            ).exists()
        )
        self.assertFalse(
            ClubSubscriptionPaymentPending.objects.filter(pk=pending.pk).exists()
        )

    def test_trial_expired_with_active_subscription_remains_operational(self) -> None:
        ClubSubscription.objects.create(
            club=self.club,
            plan=ClubPlan.BASIC,
            period=ClubSubscriptionPeriod.YEARLY,
            price=Decimal("19900.00"),
            started_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=365),
            status=ClubSubscriptionStatus.ACTIVE,
        )

        self.assertTrue(club_is_operational(self.club))

        call_command("suspend_expired_clubs")
        self.club.refresh_from_db()
        self.assertEqual(self.club.status, ClubStatus.ACTIVE)


class ClubPlanPurchaseServiceTestCase(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="player@test.local",
            password="testpass123",
        )
        self.club = Club.objects.create(
            name="Тестовый клуб",
            slug="test-club",
            city="Москва",
            address="ул. Пушкина, 1",
            email="club@test.local",
            admin_name="Администратор клуба",
        )
        self.member = ClubMember.objects.create(
            club=self.club,
            user=self.user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )

    def test_switching_plan_before_end_carries_days_and_remaining_slots(self) -> None:
        old_plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Старый",
            monthly_fee=1000,
            duration_days=30,
            max_tournaments_per_month=5,
            is_active=True,
        )
        new_plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Новый",
            monthly_fee=1500,
            duration_days=30,
            max_tournaments_per_month=4,
            is_active=True,
        )

        active_plan = purchase_member_plan(self.member, old_plan)
        active_plan.ended_at = timezone.now() + timedelta(days=10)
        active_plan.save(update_fields=["ended_at"])

        year, month = timezone.localdate().year, timezone.localdate().month
        ClubPlanSlotUsage.objects.create(
            club_member=self.member,
            plan=old_plan,
            period_year=year,
            period_month=month,
            tournaments_used=2,
        )

        switched_plan = purchase_member_plan(self.member, new_plan)
        limits = get_member_plan_limits(self.member)

        self.assertEqual(switched_plan.plan, new_plan)
        self.assertIsNotNone(switched_plan.ended_at)
        assert switched_plan.ended_at is not None
        self.assertGreaterEqual(
            switched_plan.ended_at.date(),
            (timezone.localdate() + timedelta(days=39)),
        )
        self.assertEqual(switched_plan.bonus_tournaments_balance, 3)
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.monthly_tournaments_limit, 7)
        self.assertEqual(limits.tournaments_left, 7)

    def test_purchase_uses_plan_duration_days(self) -> None:
        plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="45 дней",
            monthly_fee=1700,
            duration_days=45,
            max_tournaments_per_month=4,
            is_active=True,
        )

        assignment = purchase_member_plan(self.member, plan)

        self.assertIsNotNone(assignment.ended_at)
        assert assignment.ended_at is not None
        self.assertGreaterEqual(
            assignment.ended_at.date(),
            timezone.localdate() + timedelta(days=44),
        )

    def test_unlimited_plan_returns_unlimited_limits_and_does_not_block_after_usage(
        self,
    ) -> None:
        plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Безлимит",
            monthly_fee=2200,
            duration_days=60,
            has_unlimited_registrations=True,
            max_tournaments_per_month=None,
            is_active=True,
        )
        self.club.use_player_plans = True
        self.club.save(update_fields=["use_player_plans"])
        purchase_member_plan(self.member, plan)
        tournament = Tournament.objects.create(
            name="Многодневный клубный",
            slug="club-unlimited-plan",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            gender=TournamentGender.OPEN,
            entry_fee=0,
            is_one_day=False,
        )
        tournament.allowed_categories.create(category="amateur")

        limits = get_member_plan_limits(self.member)
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertIsNone(limits.monthly_tournaments_limit)
        self.assertTrue(can_member_register_for_tournament(self.member, tournament)[0])

        for _ in range(3):
            success, message = consume_member_tournament_limit(self.member, tournament)
            self.assertTrue(success, message)

        refreshed_limits = get_member_plan_limits(self.member)
        self.assertIsNotNone(refreshed_limits)
        assert refreshed_limits is not None
        self.assertIsNone(refreshed_limits.tournaments_left)
        self.assertEqual(refreshed_limits.tournaments_used, 3)

    def test_monthly_limit_resets_in_next_calendar_month(self) -> None:
        plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Годовой 2/месяц",
            monthly_fee=5000,
            duration_days=365,
            registration_limit_period=ClubRegistrationLimitPeriod.MONTHLY,
            max_tournaments_per_month=2,
            is_active=True,
        )
        self.club.use_player_plans = True
        self.club.save(update_fields=["use_player_plans"])
        purchase_member_plan(self.member, plan)

        current_date = timezone.localdate()
        current_limits = get_member_plan_limits(self.member, today=current_date)
        self.assertIsNotNone(current_limits)
        assert current_limits is not None
        self.assertEqual(current_limits.tournaments_left, 2)

        month_start = current_date.replace(day=1)
        current_year = month_start.year
        current_month = month_start.month
        next_month_start = (month_start + timedelta(days=32)).replace(day=1)
        ClubPlanSlotUsage.objects.filter(
            club_member=self.member,
            period_year=current_year,
            period_month=current_month,
        ).update(plan=plan, tournaments_used=2)

        next_month_limits = get_member_plan_limits(self.member, today=next_month_start)
        self.assertIsNotNone(next_month_limits)
        assert next_month_limits is not None
        self.assertEqual(next_month_limits.tournaments_left, 2)

    def test_plan_period_limit_shared_for_entire_tariff_period(self) -> None:
        plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Годовой общий лимит",
            monthly_fee=5000,
            duration_days=365,
            registration_limit_period=ClubRegistrationLimitPeriod.PLAN_PERIOD,
            max_tournaments_per_month=2,
            is_active=True,
        )
        self.club.use_player_plans = True
        self.club.save(update_fields=["use_player_plans"])
        assignment = purchase_member_plan(self.member, plan)

        started_date = timezone.localtime(assignment.started_at).date()
        ClubPlanSlotUsage.objects.create(
            club_member=self.member,
            plan=plan,
            period_year=started_date.year,
            period_month=started_date.month,
            tournaments_used=2,
        )

        next_month_date = (started_date.replace(day=1) + timedelta(days=32)).replace(
            day=1
        )
        limits = get_member_plan_limits(self.member, today=next_month_date)
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.tournaments_left, 0)

    def test_expired_plan_is_not_considered_active(self) -> None:
        plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Истёкший",
            monthly_fee=800,
            duration_days=10,
            max_tournaments_per_month=2,
            is_active=True,
        )
        assignment = purchase_member_plan(self.member, plan)
        assignment.ended_at = timezone.now() - timedelta(minutes=1)
        assignment.save(update_fields=["ended_at"])
        self.club.use_player_plans = True
        self.club.save(update_fields=["use_player_plans"])
        tournament = Tournament.objects.create(
            name="Проверка срока",
            slug="club-plan-expired-check",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            gender=TournamentGender.OPEN,
            entry_fee=0,
            is_one_day=False,
        )
        tournament.allowed_categories.create(category="amateur")

        can_register, message = can_member_register_for_tournament(
            self.member, tournament
        )

        self.assertFalse(can_register)
        self.assertEqual(message, "Для участия в турнирах клуба нужно выбрать тариф.")
