from datetime import date

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clubs.forms import ClubTournamentCreateForm
from apps.clubs.models import (
    Club,
    ClubFeePayment,
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubMember,
    ClubMemberPlan,
    ClubMemberRole,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubPlayerPlan,
    FeePaymentMethod,
)
from apps.payments.models import PaymentRecord, SavedPaymentMethod
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
        ClubMember.objects.create(
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
