import json
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import requests
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.clubs.forms import ClubPlayerPlanForm, ClubTournamentCreateForm
from apps.clubs.models import (
    Club,
    ClubFeePayment,
    ClubFeePaymentPending,
    ClubInviteLink,
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubLegalDocument,
    ClubMember,
    ClubMemberBalanceTransaction,
    ClubMemberPlan,
    ClubMemberRole,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubNotificationSettings,
    ClubPlan,
    ClubPlanSlotUsage,
    ClubPlayerPlan,
    ClubRegistrationLimitPeriod,
    ClubStatus,
    ClubSubscription,
    ClubSubscriptionPaymentPending,
    ClubSubscriptionPeriod,
    ClubSubscriptionStatus,
    FeePaymentMethod,
)
from apps.clubs.plan_services import (
    can_member_register_for_tournament,
    consume_member_tournament_limit,
    get_member_plan_limits,
    purchase_member_plan,
)
from apps.clubs.services import club_is_operational, get_current_period_label
from apps.core.models import UserTelegramLink
from apps.payments.models import PaymentRecord, SavedPaymentMethod
from apps.subscriptions.models import SubscriptionTier, UserSubscription
from apps.tournaments.models import (
    Match,
    Tournament,
    TournamentFormat,
    TournamentGender,
    TournamentPlayerResult,
    TournamentStatus,
    TournamentTeam,
    TournamentType,
)
from apps.users.models import Notification, Player, SkillLevel, User


class ClubTournamentCreateFormTestCase(TestCase):
    def setUp(self) -> None:
        self.club = Club.objects.create(
            name="Тестовый клуб",
            slug="test-club",
            city="Москва",
            address="ул. Пушкина, 1",
            email="club@test.local",
            admin_name="Администратор клуба",
        )

    def test_slug_is_generated_automatically_when_left_blank(self) -> None:
        form = ClubTournamentCreateForm(
            data={
                "name": "Клубный кубок",
                "slug": "",
                "format": TournamentFormat.WEEKEND_DAY,
                "variant": "singles",
                "entry_fee": "1000",
                "is_one_day": "",
                "city": "Москва",
                "gender": TournamentGender.MALE,
                "allowed_categories": ["amateur"],
                "tournament_type": TournamentType.REGULAR,
                "start_date": date.today().isoformat(),
                "match_days_per_round": 7,
                "fan_points_r1": 10,
                "fan_points_r2": 25,
                "fan_points_sf": 45,
                "fan_points_final": 70,
                "fan_points_winner": 100,
            },
            club=self.club,
            is_pro=False,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["slug"], "test-club-tournament")


class ClubPlayerPlanFormTestCase(TestCase):
    def test_unlimited_registrations_clear_limit(self) -> None:
        form = ClubPlayerPlanForm(
            data={
                "name": "Безлимит",
                "description": "",
                "is_active": "on",
                "monthly_fee": "1500",
                "duration_days": "45",
                "has_unlimited_registrations": "on",
                "max_tournaments_per_month": "9",
                "allow_self_change": "on",
                "sort_order": "0",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["max_tournaments_per_month"])

    def test_limited_plan_requires_monthly_limit(self) -> None:
        form = ClubPlayerPlanForm(
            data={
                "name": "Лимитный",
                "description": "",
                "is_active": "on",
                "monthly_fee": "900",
                "duration_days": "30",
                "allow_self_change": "on",
                "sort_order": "0",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("max_tournaments_per_month", form.errors)


class ClubNotificationSettingsViewTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="member@test.local",
            password="testpass123",
        )
        self.club = Club.objects.create(
            name="Тестовый клуб",
            slug="test-club-notify",
            city="Москва",
            address="ул. Пушкина, 1",
            email="club@test.local",
            admin_name="Администратор клуба",
        )
        ClubMember.objects.create(
            club=self.club,
            user=self.user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["current_club_slug"] = self.club.slug
        session.save()

    def test_page_shows_actual_delivery_destinations(self) -> None:
        response = self.client.get(
            reverse("clubs:my_notification_settings"),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "member@test.local")
        self.assertContains(response, "Бот не подключён")
        self.assertContains(response, "Игроку отсюда приходят")

    def test_telegram_channel_requires_connected_bot(self) -> None:
        response = self.client.post(
            reverse("clubs:my_notification_settings"),
            {
                "is_enabled": "on",
                "email_enabled": "on",
                "telegram_enabled": "on",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        settings_obj = ClubNotificationSettings.objects.get(
            user=self.user,
            club=self.club,
        )
        self.assertFalse(settings_obj.telegram_enabled)
        self.assertContains(
            response,
            "Сначала подключите Telegram-бота в профиле",
        )

    def test_telegram_channel_is_saved_after_bot_connection(self) -> None:
        UserTelegramLink.objects.create(
            user=self.user,
            user_bot_chat_id=123456,
        )

        response = self.client.post(
            reverse("clubs:my_notification_settings"),
            {
                "is_enabled": "on",
                "email_enabled": "on",
                "telegram_enabled": "on",
            },
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        settings_obj = ClubNotificationSettings.objects.get(
            user=self.user,
            club=self.club,
        )
        self.assertTrue(settings_obj.telegram_enabled)


class ClubPlayerPlanManagementViewTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="club-admin@test.local",
            password="testpass123",
        )
        self.club = Club.objects.create(
            name="Тестовый клуб",
            slug="test-club-plans",
            city="Москва",
            address="ул. Пушкина, 1",
            email="club@test.local",
            admin_name="Администратор клуба",
        )
        ClubMember.objects.create(
            club=self.club,
            user=self.user,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )
        self.client.force_login(self.user)

    def test_admin_can_create_plan_with_duration_and_unlimited_flag(self) -> None:
        response = self.client.post(
            reverse("clubs:plan_create", kwargs={"slug": self.club.slug}),
            data={
                "name": "Премиум",
                "description": "Описание",
                "is_active": "on",
                "monthly_fee": "2500",
                "duration_days": "90",
                "has_unlimited_registrations": "on",
                "max_tournaments_per_month": "",
                "allow_self_change": "on",
                "sort_order": "3",
            },
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        plan = ClubPlayerPlan.objects.get(club=self.club, name="Премиум")
        self.assertEqual(plan.duration_days, 90)
        self.assertTrue(plan.has_unlimited_registrations)
        self.assertIsNone(plan.max_tournaments_per_month)


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


class ClubTournamentManagementViewsTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="manager@test.local",
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
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )
        self.client.force_login(self.user)

    def _build_tournament_form_data(self, **overrides):
        data = {
            "name": "Клубный турнир",
            "slug": "club-tour",
            "format": TournamentFormat.WEEKEND_DAY,
            "variant": "singles",
            "entry_fee": "1000",
            "is_one_day": "",
            "city": "Москва",
            "gender": TournamentGender.MALE,
            "allowed_categories": ["amateur"],
            "tournament_type": TournamentType.REGULAR,
            "start_date": date.today().isoformat(),
            "match_days_per_round": 7,
            "fan_points_r1": 10,
            "fan_points_r2": 25,
            "fan_points_sf": 45,
            "fan_points_final": 70,
            "fan_points_winner": 100,
        }
        data.update(overrides)
        return data

    def test_club_tournament_can_be_edited(self) -> None:
        tournament = Tournament.objects.create(
            name="Старое имя",
            slug="club-edit",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.WEEKEND_DAY,
            status=TournamentStatus.UPCOMING,
            entry_fee=1000,
        )
        tournament.allowed_categories.create(category="amateur")

        response = self.client.post(
            reverse(
                "clubs:tournament_edit",
                kwargs={"slug": self.club.slug, "tournament_id": tournament.id},
            ),
            self._build_tournament_form_data(name="Новое имя", slug="club-edit"),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        tournament.refresh_from_db()
        self.assertEqual(tournament.name, "Новое имя")

    def test_manual_generate_bracket_works_without_registration_deadline(self) -> None:
        tournament = Tournament.objects.create(
            name="Круговой клубный турнир",
            slug="manual-start",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            entry_fee=1000,
            registration_deadline=None,
        )
        tournament.allowed_categories.create(category="amateur")

        for idx in range(2):
            user = User.objects.create_user(
                email=f"player{idx}@test.local",
                password="testpass123",
            )
            player = Player.objects.create(user=user)
            tournament.participants.add(player)

        response = self.client.post(
            reverse(
                "tournament_manage_generate_bracket",
                kwargs={"slug": tournament.slug},
            ),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        tournament.refresh_from_db()
        self.assertTrue(tournament.bracket_generated)
        self.assertGreater(tournament.matches.count(), 0)

    def test_club_tournament_can_be_cancelled_manually(self) -> None:
        tournament = Tournament.objects.create(
            name="Отменяемый турнир",
            slug="manual-cancel",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.WEEKEND_DAY,
            status=TournamentStatus.UPCOMING,
            entry_fee=1000,
        )

        response = self.client.post(
            reverse("tournament_manage_cancel", kwargs={"slug": tournament.slug}),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        tournament.refresh_from_db()
        self.assertEqual(tournament.status, TournamentStatus.CANCELLED)

    def test_manage_page_uses_club_navigation_for_club_tournament(self) -> None:
        tournament = Tournament.objects.create(
            name="Клубный турнир",
            slug="club-manage-nav",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            entry_fee=1000,
        )

        response = self.client.get(
            reverse("tournament_manage", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Тарифы игроков")
        self.assertContains(response, "Турниры")
        self.assertContains(
            response,
            reverse(
                "clubs:club_tournaments_list",
                kwargs={"slug": self.club.slug},
            ),
        )

    def test_tournament_detail_uses_club_navigation_for_club_member(self) -> None:
        tournament = Tournament.objects.create(
            name="Клубный турнир",
            slug="club-detail-nav",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            entry_fee=1000,
        )
        tournament.allowed_categories.create(category="amateur")
        player1 = Player.objects.create(user=self.user, skill_level=SkillLevel.AMATEUR)
        opponent_user = User.objects.create_user(
            email="detail-nav-opponent@test.local",
            password="testpass123",
            first_name="Иван",
            last_name="Петров",
        )
        ClubMember.objects.create(
            club=self.club,
            user=opponent_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        player2 = Player.objects.create(
            user=opponent_user, skill_level=SkillLevel.AMATEUR
        )
        tournament.participants.add(player1, player2)

        response = self.client.get(
            reverse("tournament_detail", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "На платформу")
        self.assertContains(response, self.club.name)
        self.assertContains(
            response,
            reverse(
                "clubs:player_profile",
                kwargs={"slug": self.club.slug, "player_id": player1.pk},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "clubs:player_profile",
                kwargs={"slug": self.club.slug, "player_id": player2.pk},
            ),
        )

    def test_club_member_can_open_player_profile_inside_club(self) -> None:
        Player.objects.create(user=self.user)
        opponent_user = User.objects.create_user(
            email="club-player@test.local",
            password="testpass123",
            first_name="Петр",
            last_name="Сидоров",
        )
        ClubMember.objects.create(
            club=self.club,
            user=opponent_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        player2 = Player.objects.create(user=opponent_user)

        response = self.client.get(
            reverse(
                "clubs:player_profile",
                kwargs={"slug": self.club.slug, "player_id": player2.pk},
            ),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.club.name)
        self.assertContains(response, player2.user.get_full_name())

    def test_match_detail_uses_club_navigation_for_club_member(self) -> None:
        tournament = Tournament.objects.create(
            name="Клубный турнир",
            slug="club-match-detail-nav",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            entry_fee=1000,
        )
        player1 = Player.objects.create(user=self.user)
        opponent_user = User.objects.create_user(
            email="opponent@test.local",
            password="testpass123",
            first_name="Иван",
            last_name="Петров",
        )
        ClubMember.objects.create(
            club=self.club,
            user=opponent_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        player2 = Player.objects.create(user=opponent_user)
        match = Match.objects.create(
            tournament=tournament,
            player1=player1,
            player2=player2,
        )

        response = self.client.get(
            reverse("match_detail", kwargs={"pk": match.pk}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "На платформу")
        self.assertContains(response, self.club.name)

    def test_club_member_can_open_club_tournaments_list_without_manage_access(
        self,
    ) -> None:
        member_user = User.objects.create_user(
            email="member@test.local",
            password="testpass123",
        )
        ClubMember.objects.create(
            club=self.club,
            user=member_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        Tournament.objects.create(
            name="Клубный турнир",
            slug="club-member-list",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            entry_fee=1000,
        )

        self.client.force_login(member_user)
        response = self.client.get(
            reverse(
                "clubs:club_tournaments_list",
                kwargs={"slug": self.club.slug},
            ),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Турниры клуба")
        self.assertNotContains(response, "Нет доступа к управлению клубом.")
        self.assertNotContains(response, "Создать турнир")
        self.assertNotContains(response, "Управление")

    def test_club_tournaments_list_supports_search_and_status_filter(self) -> None:
        Tournament.objects.create(
            name="Клубный микст",
            slug="club-mixed",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.ACTIVE,
            entry_fee=1000,
        )
        Tournament.objects.create(
            name="Весенний кубок",
            slug="spring-cup",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.COMPLETED,
            entry_fee=1000,
        )

        response = self.client.get(
            reverse(
                "clubs:club_tournaments_list",
                kwargs={"slug": self.club.slug},
            ),
            {"q": "микст", "status": TournamentStatus.ACTIVE},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Клубный микст")
        self.assertNotContains(response, "Весенний кубок")

    def test_club_tournaments_list_supports_category_gender_and_variant_filters(
        self,
    ) -> None:
        target = Tournament.objects.create(
            name="Женский парный любители",
            slug="women-doubles-amateur",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            gender=TournamentGender.FEMALE,
            variant="doubles",
            entry_fee=1000,
        )
        target.allowed_categories.create(category="amateur")

        other = Tournament.objects.create(
            name="Мужской одиночный новички",
            slug="men-singles-novice",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            gender=TournamentGender.MALE,
            variant="singles",
            entry_fee=1000,
        )
        other.allowed_categories.create(category="novice")

        response = self.client.get(
            reverse(
                "clubs:club_tournaments_list",
                kwargs={"slug": self.club.slug},
            ),
            {
                "category": "amateur",
                "gender": TournamentGender.FEMALE,
                "variant": "doubles",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, target.name)
        self.assertNotContains(response, other.name)

    def test_my_tournaments_includes_club_doubles_team_membership(self) -> None:
        partner_user = User.objects.create_user(
            email="my-tournaments-partner@test.local",
            password="testpass123",
            first_name="Иван",
            last_name="Петров",
        )
        partner = Player.objects.create(user=partner_user)
        ClubMember.objects.create(
            club=self.club,
            user=partner_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )

        player = Player.objects.create(user=self.user)
        tournament = Tournament.objects.create(
            name="Парный клубный турнир",
            slug="my-tournaments-doubles",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            gender=TournamentGender.OPEN,
            variant="doubles",
            entry_fee=1000,
        )
        TournamentTeam.objects.create(
            tournament=tournament,
            player1=player,
            player2=partner,
        )

        response = self.client.get(
            reverse("clubs:my_tournaments"),
            {"status": "upcoming"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, tournament.name)

    def test_member_can_open_club_plan_selection_with_payment_links(self) -> None:
        plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Платина",
            monthly_fee=2500,
            max_tournaments_per_month=6,
            is_active=True,
        )

        response = self.client.get(reverse("clubs:my_plan_change"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, plan.name)
        self.assertContains(
            response,
            reverse("clubs:my_plan_payment_preview", kwargs={"plan_id": plan.id}),
        )

    def test_payment_success_activates_paid_club_plan_for_member(self) -> None:
        plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Золото",
            monthly_fee=3000,
            max_tournaments_per_month=8,
            is_active=True,
        )

        response = self.client.get(
            reverse("payment_success"),
            {"type": "club_plan", "id": str(plan.id), "autopay": "1"},
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        assignment = ClubMemberPlan.objects.get(
            club_member__club=self.club,
            club_member__user=self.user,
            status="active",
        )
        self.assertEqual(assignment.plan, plan)
        self.assertTrue(assignment.auto_renew)
        self.assertIsNotNone(assignment.ended_at)
        session = self.client.session
        self.assertEqual(session.get("current_club_slug"), self.club.slug)

    def test_member_can_disable_club_plan_autopay_without_affecting_subscription_flag(
        self,
    ) -> None:
        SavedPaymentMethod.objects.create(
            user=self.user,
            club=self.club,
            payment_method_id="club-plan-card-1",
            card_last4="4477",
            card_network="Mastercard",
            is_active=True,
            is_default_for_subscriptions=True,
            is_default_for_club_plans=True,
        )

        response = self.client.post(
            reverse("clubs:my_plan_disable_autopay"),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        method = SavedPaymentMethod.objects.get(payment_method_id="club-plan-card-1")
        self.assertTrue(method.is_active)
        self.assertTrue(method.is_default_for_subscriptions)
        self.assertFalse(method.is_default_for_club_plans)

    def test_member_can_enable_club_plan_auto_renew_when_card_is_saved(self) -> None:
        plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Авто",
            monthly_fee=2000,
            max_tournaments_per_month=4,
            is_active=True,
        )
        assignment = purchase_member_plan(self.member, plan, auto_renew=False)
        SavedPaymentMethod.objects.create(
            user=self.user,
            club=self.club,
            payment_method_id="club-plan-card-enable-1",
            card_last4="4477",
            card_network="Mastercard",
            is_active=True,
            is_default_for_club_plans=True,
        )

        response = self.client.post(
            reverse("clubs:my_plan_enable_auto_renew"),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        assignment.refresh_from_db()
        self.assertTrue(assignment.auto_renew)

    def test_member_cannot_enable_club_plan_auto_renew_without_saved_card(self) -> None:
        plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Без карты",
            monthly_fee=2000,
            max_tournaments_per_month=4,
            is_active=True,
        )
        assignment = purchase_member_plan(self.member, plan, auto_renew=False)

        response = self.client.post(
            reverse("clubs:my_plan_enable_auto_renew"),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        assignment.refresh_from_db()
        self.assertFalse(assignment.auto_renew)

    def test_member_can_disable_club_fee_autopay_without_affecting_plan_flag(
        self,
    ) -> None:
        SavedPaymentMethod.objects.create(
            user=self.user,
            club=self.club,
            payment_method_id="club-fee-card-1",
            card_last4="1122",
            card_network="Mir",
            is_active=True,
            is_default_for_subscriptions=False,
            is_default_for_club_plans=True,
            is_default_for_club_fees=True,
        )

        response = self.client.post(
            reverse("clubs:my_fee_disable_autopay"),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        method = SavedPaymentMethod.objects.get(payment_method_id="club-fee-card-1")
        self.assertTrue(method.is_active)
        self.assertTrue(method.is_default_for_club_plans)
        self.assertFalse(method.is_default_for_club_fees)

    def test_my_fees_redirects_to_club_profile(self) -> None:
        self.player = Player.objects.create(user=self.user)
        response = self.client.get(reverse("clubs:my_fees"), secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                "clubs:player_profile",
                kwargs={"slug": self.club.slug, "player_id": self.player.id},
            ),
        )

    def test_my_payments_shows_club_plan_and_fee_history(self) -> None:
        member = ClubMember.objects.get(club=self.club, user=self.user)
        fee = ClubMembershipFee.objects.create(
            club=self.club,
            amount="400.00",
            currency="RUB",
            period="monthly",
            period_start_day=1,
            description="Ежемесячный взнос",
            is_active=True,
        )
        ClubFeePayment.objects.create(
            club=self.club,
            member=member,
            fee=fee,
            amount="400.00",
            period_label="2026-03",
            paid_at=timezone.now(),
            method=FeePaymentMethod.ONLINE,
            payment_ref="fee-payment-1",
        )
        PaymentRecord.objects.create(
            user=self.user,
            payment_type=PaymentRecord.PaymentType.CLUB_PLAN,
            item_id="12",
            item_label=f"{self.club.name}: Платина",
            amount="1000.00",
            currency="RUB",
            status="succeeded",
            yookassa_payment_id="plan-payment-1",
            metadata={"club_id": self.club.id},
        )

        response = self.client.get(reverse("clubs:my_payments"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Мои платежи")
        self.assertContains(response, "Платина")
        self.assertContains(response, "Членский взнос")
        self.assertContains(response, "fee-payment-1")

    def test_my_payments_shows_cancelled_balance_reserve_as_failed_payment(
        self,
    ) -> None:
        """Отменённый резерв баланса виден в истории как неуспешная попытка оплаты."""
        member = ClubMember.objects.get(club=self.club, user=self.user)
        ClubMemberBalanceTransaction.objects.create(
            club=self.club,
            member=member,
            direction=ClubMemberBalanceTransaction.Direction.DEBIT,
            source=ClubMemberBalanceTransaction.Source.CLUB_PLAN_PAYMENT,
            status=ClubMemberBalanceTransaction.Status.CANCELLED,
            amount=Decimal("500.00"),
            description="Оплата тарифа клуба «Платина»",
            reference="club-plan:99",
            completed_at=timezone.now(),
        )
        response = self.client.get(reverse("clubs:my_payments"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неуспешная попытка оплаты")
        self.assertContains(response, "Отменено · средства возвращены на баланс")

    def test_club_public_detail_shows_in_game_badge_when_bracket_generated(
        self,
    ) -> None:
        Tournament.objects.create(
            name="Уже стартовал",
            slug="club-public-active-badge",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            bracket_generated=True,
            entry_fee=1000,
        )

        response = self.client.get(
            reverse("clubs:club_public_detail", kwargs={"slug": self.club.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "В ИГРЕ")
        self.assertNotContains(response, "Идет набор")
        self.assertContains(response, "Подробнее")

    def test_direct_join_without_token_shows_invite_required_message(self) -> None:
        response = self.client.get(
            reverse("clubs:join", kwargs={"slug": self.club.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Вступление по приглашению")
        self.assertContains(
            response,
            "Для вступления в клуб администратор должен отправить вам приглашение.",
        )
        self.assertNotContains(response, "В ссылке отсутствует токен приглашения.")

    def test_player_can_submit_join_request_from_public_page(self) -> None:
        applicant = User.objects.create_user(
            email="applicant@test.local",
            password="testpass123",
        )
        self.client.force_login(applicant)

        response = self.client.post(
            reverse("clubs:join_request_create", kwargs={"slug": self.club.slug}),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        join_request = ClubJoinRequest.objects.get(club=self.club, user=applicant)
        self.assertEqual(join_request.status, ClubJoinRequestStatus.PENDING)
        self.assertTrue(
            Notification.objects.filter(
                user=self.user,
                message__contains="Новая заявка на вступление в клуб",
            ).exists()
        )

    def test_admin_can_approve_join_request(self) -> None:
        applicant = User.objects.create_user(
            email="approve@test.local",
            password="testpass123",
        )
        join_request = ClubJoinRequest.objects.create(
            club=self.club,
            user=applicant,
            status=ClubJoinRequestStatus.PENDING,
        )

        response = self.client.post(
            reverse(
                "clubs:join_request_approve",
                kwargs={"slug": self.club.slug, "pk": join_request.pk},
            ),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        join_request.refresh_from_db()
        self.assertEqual(join_request.status, ClubJoinRequestStatus.APPROVED)
        self.assertTrue(
            ClubMember.objects.filter(
                club=self.club,
                user=applicant,
                status=ClubMemberStatus.ACTIVE,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=applicant,
                message__contains="одобрена",
            ).exists()
        )

    def test_admin_can_reject_join_request(self) -> None:
        applicant = User.objects.create_user(
            email="reject@test.local",
            password="testpass123",
        )
        join_request = ClubJoinRequest.objects.create(
            club=self.club,
            user=applicant,
            status=ClubJoinRequestStatus.PENDING,
        )

        response = self.client.post(
            reverse(
                "clubs:join_request_reject",
                kwargs={"slug": self.club.slug, "pk": join_request.pk},
            ),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        join_request.refresh_from_db()
        self.assertEqual(join_request.status, ClubJoinRequestStatus.REJECTED)
        self.assertFalse(
            ClubMember.objects.filter(
                club=self.club,
                user=applicant,
                status=ClubMemberStatus.ACTIVE,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=applicant,
                message__contains="отклонена",
            ).exists()
        )

    def test_join_request_list_shows_clickable_global_profile_link(self) -> None:
        applicant = User.objects.create_user(
            email="profile-link@test.local",
            password="testpass123",
            first_name="Иван",
            last_name="Петров",
            phone="+79990000000",
        )
        player = Player.objects.create(
            user=applicant,
            city="Москва",
            skill_level="amateur",
            total_points=1234.5,
        )
        ClubJoinRequest.objects.create(
            club=self.club,
            user=applicant,
            status=ClubJoinRequestStatus.PENDING,
        )

        response = self.client.get(
            reverse("clubs:invites_list", kwargs={"slug": self.club.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("profile", kwargs={"pk": player.pk}))
        self.assertContains(response, "Иван Петров")
        self.assertContains(response, "Москва")
        self.assertContains(response, "+79990000000")

    def test_invites_list_shows_inline_action_forms(self) -> None:
        response = self.client.get(
            reverse("clubs:invites_list", kwargs={"slug": self.club.slug}),
            secure=True,
        )
        page = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="invite_action"', count=3, html=False)
        self.assertContains(response, 'value="create_link"', html=False)
        self.assertContains(response, 'value="invite_email"', html=False)
        self.assertContains(response, 'value="import_csv"', html=False)
        self.assertContains(response, 'name="email"', html=False)
        self.assertContains(response, 'name="file"', html=False)
        self.assertContains(response, "Скачать шаблон")
        self.assertContains(
            response,
            reverse("clubs:invite_import_template", kwargs={"slug": self.club.slug}),
        )
        self.assertLess(page.index("Инвайт-ссылки"), page.index("Быстрые действия"))

    def test_invite_import_template_download(self) -> None:
        response = self.client.get(
            reverse("clubs:invite_import_template", kwargs={"slug": self.club.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn(
            f"{self.club.slug}-invite-template.csv",
            response["Content-Disposition"],
        )
        self.assertIn(b"email", response.content)

    def test_invites_list_can_create_link_inline(self) -> None:
        response = self.client.post(
            reverse("clubs:invites_list", kwargs={"slug": self.club.slug}),
            {
                "invite_action": "create_link",
                "expires_days": "7",
                "max_uses": "3",
            },
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("clubs:invites_list", kwargs={"slug": self.club.slug}),
            fetch_redirect_response=False,
        )
        link = ClubInviteLink.objects.get(club=self.club)
        self.assertEqual(link.max_uses, 3)
        self.assertTrue(link.is_active)
        self.assertIsNotNone(link.expires_at)

    def test_invites_list_can_invite_user_by_email_inline(self) -> None:
        invitee = User.objects.create_user(
            email="invite-inline@test.local",
            password="testpass123",
        )

        response = self.client.post(
            reverse("clubs:invites_list", kwargs={"slug": self.club.slug}),
            {
                "invite_action": "invite_email",
                "email": invitee.email,
            },
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("clubs:invites_list", kwargs={"slug": self.club.slug}),
            fetch_redirect_response=False,
        )
        self.assertTrue(
            ClubMember.objects.filter(
                club=self.club,
                user=invitee,
                status=ClubMemberStatus.INVITED,
            ).exists()
        )

    def test_invites_list_can_import_csv_inline(self) -> None:
        first_user = User.objects.create_user(
            email="import-inline-1@test.local",
            password="testpass123",
        )
        second_user = User.objects.create_user(
            email="import-inline-2@test.local",
            password="testpass123",
        )
        upload = SimpleUploadedFile(
            "club-invites.csv",
            (f"{first_user.email}\n" f"{second_user.email}\n").encode(),
            content_type="text/csv",
        )

        response = self.client.post(
            reverse("clubs:invites_list", kwargs={"slug": self.club.slug}),
            {
                "invite_action": "import_csv",
                "file": upload,
            },
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("clubs:invites_list", kwargs={"slug": self.club.slug}),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            ClubMember.objects.filter(
                club=self.club,
                status=ClubMemberStatus.INVITED,
                user__in=[first_user, second_user],
            ).count(),
            2,
        )

    def test_invites_list_shows_activate_and_delete_actions(self) -> None:
        inactive_link = ClubInviteLink.objects.create(
            club=self.club,
            token="inactive-link-token",
            created_by=self.user,
            expires_at=timezone.now() + timedelta(days=3),
            is_active=False,
        )
        expired_link = ClubInviteLink.objects.create(
            club=self.club,
            token="expired-link-token",
            created_by=self.user,
            expires_at=timezone.now() - timedelta(days=1),
            is_active=True,
        )

        response = self.client.get(
            reverse("clubs:invites_list", kwargs={"slug": self.club.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse(
                "clubs:invite_activate",
                kwargs={"slug": self.club.slug, "pk": inactive_link.pk},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "clubs:invite_delete",
                kwargs={"slug": self.club.slug, "pk": inactive_link.pk},
            ),
        )
        self.assertContains(
            response,
            reverse(
                "clubs:invite_delete",
                kwargs={"slug": self.club.slug, "pk": expired_link.pk},
            ),
        )
        self.assertNotContains(
            response,
            reverse(
                "clubs:invite_activate",
                kwargs={"slug": self.club.slug, "pk": expired_link.pk},
            ),
        )

    def test_invite_activate_reactivates_inactive_link(self) -> None:
        link = ClubInviteLink.objects.create(
            club=self.club,
            token="reactivate-link-token",
            created_by=self.user,
            expires_at=timezone.now() + timedelta(days=5),
            is_active=False,
        )

        response = self.client.post(
            reverse(
                "clubs:invite_activate",
                kwargs={"slug": self.club.slug, "pk": link.pk},
            ),
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("clubs:invites_list", kwargs={"slug": self.club.slug}),
            fetch_redirect_response=False,
        )
        link.refresh_from_db()
        self.assertTrue(link.is_active)

    def test_invite_delete_removes_expired_link(self) -> None:
        link = ClubInviteLink.objects.create(
            club=self.club,
            token="expired-delete-link-token",
            created_by=self.user,
            expires_at=timezone.now() - timedelta(days=2),
            is_active=True,
        )

        response = self.client.post(
            reverse(
                "clubs:invite_delete",
                kwargs={"slug": self.club.slug, "pk": link.pk},
            ),
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("clubs:invites_list", kwargs={"slug": self.club.slug}),
            fetch_redirect_response=False,
        )
        self.assertFalse(ClubInviteLink.objects.filter(pk=link.pk).exists())

    def test_club_dashboard_season_points_use_tournament_fan_points(self) -> None:
        player = Player.objects.create(user=self.user)
        tournament = Tournament.objects.create(
            name="Завершенный клубный турнир",
            slug="club-season-points-fan",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            end_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.COMPLETED,
            entry_fee=1000,
        )
        opponent_user = User.objects.create_user(
            email="season-opponent@test.local",
            password="testpass123",
        )
        opponent = Player.objects.create(user=opponent_user)
        Match.objects.create(
            tournament=tournament,
            player1=player,
            player2=opponent,
            status=Match.MatchStatus.COMPLETED,
            winner=player,
            rating_delta_player1=-13.2,
            rating_delta_player2=13.2,
            completed_datetime=timezone.now(),
        )
        TournamentPlayerResult.objects.create(
            tournament=tournament,
            player=player,
            round_eliminated=TournamentPlayerResult.RoundEliminated.WINNER,
            fan_points=100,
        )

        response = self.client.get(
            reverse("clubs:my_dashboard"),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["season_points"].current_season_points,
            100,
        )
        self.assertEqual(
            response.context["season_points_data"][-1]["season_points"],
            100,
        )
        self.assertNotEqual(
            response.context["season_points"].current_season_points,
            int(round(float(response.context["club_points_now"]))),
        )

    def test_search_participants_works_for_non_tvd_club_tournament(self) -> None:
        tournament = Tournament.objects.create(
            name="Круговой клубный турнир",
            slug="club-search",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            entry_fee=1000,
        )
        user = User.objects.create_user(
            email="findme@test.local",
            password="testpass123",
            first_name="Леонид",
            last_name="Ермолаев",
        )
        Player.objects.create(user=user)
        ClubMember.objects.create(
            club=self.club,
            user=user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )

        response = self.client.get(
            reverse(
                "tournament_manage_search_participants",
                kwargs={"slug": tournament.slug},
            ),
            {"q": "Леон"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 1)
        self.assertIn("Леонид", payload["results"][0]["display"])

    def test_search_participants_is_limited_to_current_club_members(self) -> None:
        tournament = Tournament.objects.create(
            name="Клубный поиск",
            slug="club-search-scope",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.ROUND_ROBIN,
            status=TournamentStatus.UPCOMING,
            entry_fee=1000,
        )
        club_user = User.objects.create_user(
            email="clubmember@test.local",
            password="testpass123",
            first_name="Иван",
            last_name="Иванов",
        )
        ClubMember.objects.create(
            club=self.club,
            user=club_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        Player.objects.create(user=club_user)

        external_user = User.objects.create_user(
            email="external@test.local",
            password="testpass123",
            first_name="Игорь",
            last_name="Иванченко",
        )
        Player.objects.create(user=external_user)

        response = self.client.get(
            reverse(
                "tournament_manage_search_participants",
                kwargs={"slug": tournament.slug},
            ),
            {"q": "Ива"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 1)
        self.assertIn("Иванов", payload["results"][0]["display"])
        self.assertNotIn("Иванченко", payload["results"][0]["display"])


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
        session = self.client.session
        session["club_plan_payment_pending"] = {
            "payment_id": "club-plan-pay-002",
            "plan_id": self.plan.id,
            "club_slug": self.club.slug,
            "enable_autopay": "1",
            "next": "",
            "amount": "1200.00",
            "balance_amount": "0.00",
            "total_amount": "1200.00",
            "balance_transaction_id": "",
        }
        session.save()

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
        pending = ClubFeePaymentPending.objects.create(
            payment_id="club-fee-pay-002",
            club=self.club,
            fee=self.payment_settings,
            member=self.member,
            amount=Decimal("300.00"),
            period_label=get_current_period_label(self.payment_settings),
        )
        session = self.client.session
        session["club_fee_payment_pending"] = {
            "payment_id": pending.payment_id,
            "club_slug": self.club.slug,
            "enable_autopay": "1",
            "next": "",
            "balance_transaction_id": "",
            "balance_amount": "0.00",
            "total_amount": "300.00",
        }
        session.save()

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
                        "card": {
                            "last4": "7788",
                            "expiry_month": "11",
                            "expiry_year": "2031",
                            "card_type": "Mir",
                        },
                    }
                },
            ),
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
            max_tournaments=20,
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
            tournament_registration_balance=20,
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
        session = self.client.session
        session["yookassa_pending"] = {
            "payment_id": "club-pay-002",
            "payment_type": "tournament",
            "item_id": str(self.tournament.id),
            "next": "",
            "amount": "800.00",
            "balance_amount": "0.00",
            "total_amount": "800.00",
            "club_id": str(self.club.id),
            "club_slug": self.club.slug,
        }
        session.save()

        with (
            patch(
                "apps.payments.views.get_payment_status_with_credentials",
                return_value="succeeded",
            ) as get_club_status,
            patch("apps.payments.views.get_payment_status") as get_global_status,
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
