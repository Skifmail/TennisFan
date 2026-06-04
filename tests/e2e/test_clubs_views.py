"""E2E: страницы и сценарии клуба."""

import os
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlencode

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    Club,
    ClubFeePayment,
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
    ClubPlayerPlan,
    FeePaymentMethod,
)
from apps.clubs.plan_services import (
    purchase_member_plan,
)
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


@override_settings(TELEGRAM_ENABLED=True)
class ClubNotificationSettingsViewTestCase(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._telegram_env = patch.dict(
            os.environ, {"TELEGRAM_ENABLED": "true"}, clear=False
        )
        cls._telegram_env.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._telegram_env.stop()
        super().tearDownClass()

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


class MyClubsViewTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="multi-club@test.local",
            password="testpass123",
            first_name="Иван",
        )
        Player.objects.create(user=self.user)
        self.club_alpha = Club.objects.create(
            name="Альфа Клуб",
            slug="alpha-club",
            city="Москва",
            address="ул. Альфа, 1",
            email="alpha@test.local",
            admin_name="Администратор Альфа",
            description="Первый клуб пользователя.",
        )
        self.club_beta = Club.objects.create(
            name="Бета Клуб",
            slug="beta-club",
            city="Сочи",
            address="ул. Бета, 2",
            email="beta@test.local",
            admin_name="Администратор Бета",
            description="Второй клуб пользователя.",
        )
        self.club_removed = Club.objects.create(
            name="Старый клуб",
            slug="old-club",
            city="Казань",
            address="ул. Архивная, 3",
            email="old@test.local",
            admin_name="Архивный админ",
        )
        ClubMember.objects.create(
            club=self.club_alpha,
            user=self.user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        ClubMember.objects.create(
            club=self.club_beta,
            user=self.user,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )
        ClubMember.objects.create(
            club=self.club_removed,
            user=self.user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.REMOVED,
        )
        self.client.force_login(self.user)

    def test_my_clubs_page_lists_active_memberships_and_current_club(self) -> None:
        session = self.client.session
        session["current_club_slug"] = self.club_beta.slug
        session.save()

        response = self.client.get(reverse("clubs:my_clubs"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Мои клубы")
        self.assertContains(response, self.club_alpha.name)
        self.assertContains(response, self.club_beta.name)
        self.assertNotContains(response, self.club_removed.name)
        self.assertContains(
            response,
            reverse("clubs:set_current_club", kwargs={"slug": self.club_alpha.slug}),
        )
        self.assertContains(
            response,
            reverse("clubs:set_current_club", kwargs={"slug": self.club_beta.slug}),
        )
        self.assertContains(response, "Текущий клуб")
        self.assertContains(response, "Личный кабинет")
        self.assertContains(response, "Панель управления")

    def test_base_dropdown_links_to_my_clubs_page(self) -> None:
        response = self.client.get(reverse("clubs:club_discover"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("clubs:my_clubs"))
        self.assertContains(response, "Мои клубы")

    def test_club_my_home_redirects_to_my_clubs_page(self) -> None:
        response = self.client.get(reverse("clubs:club_my_home"), secure=True)

        self.assertRedirects(
            response,
            reverse("clubs:my_clubs"),
            fetch_redirect_response=False,
        )

    def test_opening_other_club_public_page_switches_current_club_for_dashboard(
        self,
    ) -> None:
        session = self.client.session
        session["current_club_slug"] = self.club_alpha.slug
        session.save()

        response = self.client.get(
            reverse("clubs:club_public_detail", kwargs={"slug": self.club_beta.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertEqual(session.get("current_club_slug"), self.club_beta.slug)
        dashboard_response = self.client.get(reverse("clubs:my_dashboard"), secure=True)
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertEqual(dashboard_response.context["club"].slug, self.club_beta.slug)

    def test_my_clubs_public_page_link_switches_current_club_before_opening(
        self,
    ) -> None:
        session = self.client.session
        session["current_club_slug"] = self.club_alpha.slug
        session.save()

        response = self.client.get(reverse("clubs:my_clubs"), secure=True)

        public_url = reverse(
            "clubs:club_public_detail",
            kwargs={"slug": self.club_beta.slug},
        )
        switch_url = reverse(
            "clubs:set_current_club",
            kwargs={"slug": self.club_beta.slug},
        )
        expected_open_url = f"{switch_url}?{urlencode({'next': public_url})}"
        self.assertContains(response, expected_open_url)

        open_response = self.client.get(
            expected_open_url,
            secure=True,
            follow=True,
        )

        self.assertEqual(open_response.status_code, 200)
        self.assertEqual(open_response.request["PATH_INFO"], public_url)
        session = self.client.session
        self.assertEqual(session.get("current_club_slug"), self.club_beta.slug)

    def test_club_top_nav_renders_switcher_with_other_clubs(self) -> None:
        session = self.client.session
        session["current_club_slug"] = self.club_alpha.slug
        session.save()

        response = self.client.get(
            reverse("clubs:my_dashboard"),
            secure=True,
        )

        personal_url = reverse("clubs:my_dashboard")
        switch_url = reverse(
            "clubs:set_current_club",
            kwargs={"slug": self.club_beta.slug},
        )
        expected_switch_url = f"{switch_url}?{urlencode({'next': personal_url})}"

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "club-top-nav__switcher")
        self.assertContains(response, "club-mobile-switcher-toggle")
        self.assertContains(response, self.club_beta.name)
        self.assertContains(response, expected_switch_url)
        self.assertContains(response, reverse("clubs:my_clubs"))


class ClubProfileSubscriptionIsolationTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.owner = User.objects.create_user(
            email="club-owner-sub@test.local",
            password="testpass123",
            first_name="Иван",
        )
        self.viewer = User.objects.create_user(
            email="club-viewer-sub@test.local",
            password="testpass123",
            first_name="Петр",
        )
        self.owner_player = Player.objects.create(user=self.owner)
        Player.objects.create(user=self.viewer)
        self.club = Club.objects.create(
            name="Клуб изоляции",
            slug="club-isolation",
            city="Москва",
            address="ул. Тестовая, 7",
            email="club-isolation@test.local",
            admin_name="Администратор клуба",
        )
        ClubMember.objects.create(
            club=self.club,
            user=self.owner,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        ClubMember.objects.create(
            club=self.club,
            user=self.viewer,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )
        tier = SubscriptionTier.objects.create(
            name="GLOBAL-PLATINUM-TEST",
            price=Decimal("4900.00"),
            fancoin_per_purchase=10,
            has_badge=True,
            can_see_stats=True,
        )
        UserSubscription.objects.create(
            user=self.owner,
            tier=tier,
            start_date=timezone.now() - timedelta(days=1),
            end_date=timezone.now() + timedelta(days=30),
            is_active=True,
        )

    def test_my_club_dashboard_does_not_show_platform_subscription_name(self) -> None:
        self.client.force_login(self.owner)
        session = self.client.session
        session["current_club_slug"] = self.club.slug
        session.save()

        response = self.client.get(reverse("clubs:my_dashboard"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "GLOBAL-PLATINUM-TEST")
        self.assertContains(response, "У вас нет активного клубного тарифа")

    def test_other_member_club_profile_does_not_show_platform_subscription_name(
        self,
    ) -> None:
        self.client.force_login(self.viewer)
        session = self.client.session
        session["current_club_slug"] = self.club.slug
        session.save()

        response = self.client.get(
            reverse(
                "clubs:player_profile",
                kwargs={"slug": self.club.slug, "player_id": self.owner_player.pk},
            ),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "GLOBAL-PLATINUM-TEST")


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
                "registration_limit_period": "monthly",
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

    def test_platform_staff_without_club_role_cannot_manage_club_tournament(
        self,
    ) -> None:
        """Сотрудник платформы без роли в клубе не видит страницу управления клубным турниром."""
        staff = User.objects.create_user(
            email="platform-staff-no-club@test.local",
            password="testpass123",
            is_staff=True,
        )
        tournament = Tournament.objects.create(
            name="Клубный ТВД",
            slug="staff-manage-forbidden",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.WEEKEND_DAY,
            status=TournamentStatus.UPCOMING,
            entry_fee=0,
        )
        tournament.allowed_categories.create(category="amateur")
        self.client.force_login(staff)
        response = self.client.get(
            reverse("tournament_manage", kwargs={"slug": tournament.slug}),
            secure=True,
        )
        self.assertEqual(response.status_code, 403)

    def test_platform_staff_without_club_role_cannot_post_generate_groups(self) -> None:
        """POST «сформировать группы» для клубного ТВД недоступен без прав клуба."""
        staff = User.objects.create_user(
            email="platform-staff-post@test.local",
            password="testpass123",
            is_staff=True,
        )
        tournament = Tournament.objects.create(
            name="Клубный ТВД POST",
            slug="staff-post-forbidden",
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format=TournamentFormat.WEEKEND_DAY,
            status=TournamentStatus.UPCOMING,
            entry_fee=0,
        )
        tournament.allowed_categories.create(category="amateur")
        self.client.force_login(staff)
        response = self.client.post(
            reverse(
                "tournament_manage_generate_groups",
                kwargs={"slug": tournament.slug},
            ),
            secure=True,
        )
        self.assertRedirects(
            response,
            reverse("tournament_list"),
            status_code=302,
            target_status_code=200,
            fetch_redirect_response=False,
        )

    def test_platform_staff_can_manage_platform_tvd_tournament(self) -> None:
        """Сотрудник платформы может управлять турниром без клуба (организатор — платформа)."""
        staff = User.objects.create_user(
            email="platform-staff-open@test.local",
            password="testpass123",
            is_staff=True,
        )
        tournament = Tournament.objects.create(
            name="Платформенный ТВД",
            slug="platform-open-tvd",
            city="Москва",
            club=None,
            start_date=date.today(),
            format=TournamentFormat.WEEKEND_DAY,
            status=TournamentStatus.UPCOMING,
            entry_fee=0,
        )
        tournament.allowed_categories.create(category="amateur")
        self.client.force_login(staff)
        response = self.client.get(
            reverse("tournament_manage", kwargs={"slug": tournament.slug}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)

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
        ClubMembershipFee.objects.create(
            club=self.club,
            amount=Decimal("300.00"),
            currency="RUB",
            period="monthly",
            period_start_day=1,
            payment_provider=ClubMembershipFee.PaymentProvider.YOOKASSA,
            payment_shop_id="club-shop-plan",
            payment_api_key="club-secret-plan",
            is_active=True,
        )
        ClubLegalDocument.objects.create(
            club=self.club,
            title="Оферта",
            content="Текст",
            version="1.0",
            is_published=True,
        )
        with (
            patch(
                "apps.clubs.views.subscription.decrypt_secret",
                return_value="club-secret-plan",
            ),
            patch(
                "apps.clubs.views.subscription.create_payment_with_credentials",
                return_value=("club-plan-success-001", "https://pay.example/plan"),
            ),
            patch("apps.clubs.views.subscription.create_payment"),
        ):
            self.client.post(
                reverse("clubs:my_plan_payment_process"),
                {
                    "id": str(plan.id),
                    "offer_accepted": "on",
                    "club_offer_accepted": "on",
                    "enable_autopay": "on",
                },
                secure=True,
            )

        with (
            patch(
                "apps.clubs.views.subscription.decrypt_secret",
                return_value="club-secret-plan",
            ),
            patch(
                "apps.clubs.views.subscription.get_payment_status_with_credentials",
                return_value="succeeded",
            ),
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
        assignment = ClubMemberPlan.objects.get(
            club_member__club=self.club,
            club_member__user=self.user,
            status="active",
        )
        self.assertEqual(assignment.plan, plan)
        self.assertTrue(assignment.auto_renew)
        self.assertIsNotNone(assignment.ended_at)

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

    def test_club_public_detail_links_to_all_tournaments_catalog(self) -> None:
        self.client.logout()
        response = self.client.get(
            reverse("clubs:club_public_detail", kwargs={"slug": self.club.slug}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Все турниры клуба")
        self.assertContains(
            response,
            reverse(
                "clubs:club_tournaments_list",
                kwargs={"slug": self.club.slug},
            ),
        )

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
