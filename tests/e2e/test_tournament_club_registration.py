"""E2E: регистрация на клубные турниры."""

from datetime import date, timedelta
from typing import cast
from urllib.parse import urlencode

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    Club,
    ClubMember,
    ClubMemberPlan,
    ClubMemberRole,
    ClubMemberStatus,
    ClubPlayerPlan,
)
from apps.clubs.plan_services import assign_member_plan
from apps.subscriptions.models import (
    SubscriptionTier,
    UserSubscription,
)
from apps.tournaments.models import (
    Tournament,
    TournamentStatus,
)
from apps.users.models import Player, User


class ClubTournamentRegistrationWithoutGlobalSubscriptionTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="club-member@test.local",
            password="testpass123",
            first_name="Леонид",
            last_name="Ермолаев",
            phone="+79990000000",
        )
        self.player = Player.objects.create(
            user=self.user,
            skill_level="amateur",
            birth_date=date(1990, 1, 1),
            is_verified=True,
        )
        self.club = Club.objects.create(
            name="Спартак",
            slug="spartak-club",
            city="Москва",
            address="ул. Спортивная, 1",
            email="club@test.local",
            admin_name="Админ клуба",
        )
        self.member = ClubMember.objects.create(
            club=self.club,
            user=self.user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        self.plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Стандарт",
            monthly_fee=1000,
            max_tournaments_per_month=3,
            is_active=True,
        )
        assign_member_plan(self.member, self.plan)
        self.client.force_login(self.user)

    def _create_club_tournament(
        self,
        *,
        slug: str,
        entry_fee: int = 0,
        is_one_day: bool = False,
        variant: str = "singles",
    ) -> Tournament:
        tournament = Tournament.objects.create(
            name=f"Турнир {slug}",
            slug=slug,
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.UPCOMING,
            gender="open",
            is_one_day=is_one_day,
            entry_fee=entry_fee,
            variant=variant,
        )
        tournament.allowed_categories.create(category="amateur")
        return cast(Tournament, tournament)

    def _clear_member_plan(self) -> None:
        ClubMemberPlan.objects.filter(club_member=self.member).delete()

    def _create_global_subscription(self, *, slots: int = 5) -> None:
        tier = SubscriptionTier.objects.create(
            name="club-test-tier",
            display_name="Club Test Tier",
            price=990,
            fancoin_per_purchase=slots,
            duration_days=30,
            is_visible=True,
        )
        UserSubscription.objects.create(
            user=self.user,
            tier=tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
            is_active=True,
            fancoin_balance=slots,
        )

    def _assert_tournament_payment_redirect(
        self,
        response,
        tournament: Tournament,
        *,
        next_url: str = "",
    ) -> None:
        params: dict[str, str | int] = {
            "type": "tournament",
            "id": tournament.id,
        }
        if next_url:
            params["next"] = next_url
        expected_url = f"{reverse('payment_preview')}?{urlencode(params)}"
        self.assertRedirects(
            response,
            expected_url,
            fetch_redirect_response=False,
        )

    def test_member_can_register_for_multiday_club_tournament_without_global_subscription(
        self,
    ) -> None:
        tournament = self._create_club_tournament(
            slug="club-multiday-no-global-sub",
            entry_fee=0,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("tournament_detail", kwargs={"slug": tournament.slug}),
            fetch_redirect_response=False,
        )
        self.assertTrue(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_can_register_doubles_team_for_club_tournament_without_global_subscription(
        self,
    ) -> None:
        partner_user = User.objects.create_user(
            email="club-partner@test.local",
            password="testpass123",
            first_name="Иван",
            last_name="Петров",
            phone="+79990000001",
        )
        partner = Player.objects.create(
            user=partner_user,
            skill_level="amateur",
            birth_date=date(1991, 1, 1),
        )
        partner_member = ClubMember.objects.create(
            club=self.club,
            user=partner_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        assign_member_plan(partner_member, self.plan)

        tournament = self._create_club_tournament(
            slug="club-doubles-no-global-sub",
            entry_fee=0,
            is_one_day=False,
            variant="doubles",
        )

        response = self.client.post(
            reverse("tournament_register_doubles", kwargs={"slug": tournament.slug}),
            {"action": "add_partner", "partner_id": str(partner.pk)},
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("tournament_detail", kwargs={"slug": tournament.slug}),
            fetch_redirect_response=False,
        )
        self.assertTrue(
            tournament.teams.filter(player1=self.player, player2=partner).exists()
        )

    def test_club_doubles_registration_uses_club_shell_without_global_footer(
        self,
    ) -> None:
        tournament = self._create_club_tournament(
            slug="club-doubles-shell",
            entry_fee=0,
            is_one_day=False,
            variant="doubles",
        )

        response = self.client.get(
            reverse("tournament_register_doubles", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.club.name)
        self.assertContains(response, "Личный кабинет")
        self.assertContains(response, "body--club-panel")
        self.assertNotContains(response, "РЕЙТИНГ")

    def test_club_doubles_partner_search_is_limited_to_active_club_members(
        self,
    ) -> None:
        club_partner_user = User.objects.create_user(
            email="club-search-partner@test.local",
            password="testpass123",
            first_name="Анна",
            last_name="Клубная",
            phone="+79990000002",
        )
        club_partner = Player.objects.create(
            user=club_partner_user,
            skill_level="amateur",
            birth_date=date(1991, 1, 1),
        )
        ClubMember.objects.create(
            club=self.club,
            user=club_partner_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )

        outsider_user = User.objects.create_user(
            email="platform-search-partner@test.local",
            password="testpass123",
            first_name="Анна",
            last_name="Платформа",
            phone="+79990000003",
        )
        outsider = Player.objects.create(
            user=outsider_user,
            skill_level="amateur",
            birth_date=date(1992, 1, 1),
        )

        tournament = self._create_club_tournament(
            slug="club-doubles-search",
            entry_fee=0,
            is_one_day=False,
            variant="doubles",
        )

        response = self.client.get(
            reverse("tournament_register_doubles", kwargs={"slug": tournament.slug}),
            {"q": "Анна"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, club_partner.user.last_name)
        self.assertNotContains(response, outsider.user.last_name)
        self.assertNotContains(response, f'value="{outsider.pk}"')

    def test_club_doubles_registration_rejects_partner_outside_club(self) -> None:
        outsider_user = User.objects.create_user(
            email="outsider-post@test.local",
            password="testpass123",
            first_name="Анна",
            last_name="Внешняя",
        )
        outsider = Player.objects.create(
            user=outsider_user,
            skill_level="amateur",
            birth_date=date(1992, 1, 1),
        )

        tournament = self._create_club_tournament(
            slug="club-doubles-post-check",
            entry_fee=0,
            is_one_day=False,
            variant="doubles",
        )

        response = self.client.post(
            reverse("tournament_register_doubles", kwargs={"slug": tournament.slug}),
            {"action": "add_partner", "partner_id": str(outsider.pk)},
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Для клубного турнира можно выбрать партнёра только из активных участников клуба.",
        )
        self.assertFalse(
            tournament.teams.filter(player1=self.player, player2=outsider).exists()
        )

    def test_member_without_club_plan_cannot_register_for_free_multiday_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        tournament = self._create_club_tournament(
            slug="club-multiday-free-no-plan",
            entry_fee=0,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Для участия в турнирах клуба нужно выбрать тариф.",
        )
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_with_expired_club_plan_cannot_register_for_free_multiday_club_tournament(
        self,
    ) -> None:
        assignment = ClubMemberPlan.objects.get(
            club_member=self.member, status="active"
        )
        assignment.ended_at = timezone.now() - timedelta(minutes=1)
        assignment.save(update_fields=["ended_at"])
        tournament = self._create_club_tournament(
            slug="club-multiday-free-expired-plan",
            entry_fee=0,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Для участия в турнирах клуба нужно выбрать тариф.",
        )
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_without_club_plan_is_redirected_to_payment_for_paid_multiday_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        tournament = self._create_club_tournament(
            slug="club-multiday-paid-no-plan",
            entry_fee=700,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self._assert_tournament_payment_redirect(response, tournament)
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_without_club_plan_can_register_for_free_one_day_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        tournament = self._create_club_tournament(
            slug="club-one-day-free-no-plan",
            entry_fee=0,
            is_one_day=True,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("tournament_detail", kwargs={"slug": tournament.slug}),
            fetch_redirect_response=False,
        )
        self.assertTrue(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_without_club_plan_is_redirected_to_payment_for_paid_one_day_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        tournament = self._create_club_tournament(
            slug="club-one-day-paid-no-plan",
            entry_fee=500,
            is_one_day=True,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self._assert_tournament_payment_redirect(response, tournament)
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_global_subscription_does_not_replace_club_plan_for_free_multiday_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        self._create_global_subscription(slots=10)
        tournament = self._create_club_tournament(
            slug="club-multiday-free-global-sub",
            entry_fee=0,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Для участия в турнирах клуба нужно выбрать тариф.",
        )
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_global_subscription_does_not_replace_club_plan_for_paid_multiday_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        self._create_global_subscription(slots=10)
        tournament = self._create_club_tournament(
            slug="club-multiday-paid-global-sub",
            entry_fee=900,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self._assert_tournament_payment_redirect(response, tournament)
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_club_member_with_plan_can_register_for_paid_multiday_club_tournament_without_global_subscription(
        self,
    ) -> None:
        tournament = self._create_club_tournament(
            slug="club-multiday-paid-with-plan",
            entry_fee=850,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("tournament_detail", kwargs={"slug": tournament.slug}),
            fetch_redirect_response=False,
        )
        self.assertTrue(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_without_club_plan_can_register_when_club_player_plans_disabled(
        self,
    ) -> None:
        self._clear_member_plan()
        self.club.use_player_plans = False
        self.club.save(update_fields=["use_player_plans"])
        tournament = self._create_club_tournament(
            slug="club-multiday-free-plans-disabled",
            entry_fee=0,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("tournament_detail", kwargs={"slug": tournament.slug}),
            fetch_redirect_response=False,
        )
        self.assertTrue(tournament.participants.filter(pk=self.player.pk).exists())

    def test_paid_club_tournament_redirects_to_payment_when_club_player_plans_disabled(
        self,
    ) -> None:
        self._clear_member_plan()
        self.club.use_player_plans = False
        self.club.save(update_fields=["use_player_plans"])
        tournament = self._create_club_tournament(
            slug="club-multiday-paid-plans-disabled",
            entry_fee=950,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self._assert_tournament_payment_redirect(response, tournament)
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_without_club_plan_is_redirected_to_payment_for_paid_doubles_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        tournament = self._create_club_tournament(
            slug="club-doubles-paid-no-plan",
            entry_fee=650,
            is_one_day=False,
            variant="doubles",
        )
        next_url = f"https://testserver{reverse('tournament_register_doubles', kwargs={'slug': tournament.slug})}"

        response = self.client.get(
            reverse("tournament_register_doubles", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self._assert_tournament_payment_redirect(
            response,
            tournament,
            next_url=next_url,
        )
