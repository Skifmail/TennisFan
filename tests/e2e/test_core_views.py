"""E2E: главная, каталог клубов, экспорт, поддержка."""

import csv
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    Club,
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubMember,
    ClubMemberRole,
    ClubMemberStatus,
)
from apps.core.models import SupportThread
from apps.tournaments.models import Match, Tournament, TournamentStatus
from apps.users.models import Player, User


class HomeRecentMatchesWidgetTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.player1 = Player.objects.create(
            user=User.objects.create_user(
                email="home-player1@test.local",
                password="testpass123",
                first_name="Леонид",
                last_name="Ермолаев",
            )
        )
        self.player2 = Player.objects.create(
            user=User.objects.create_user(
                email="home-player2@test.local",
                password="testpass123",
                first_name="Александр",
                last_name="Шатайло",
            )
        )

    def test_recent_matches_api_includes_club_name_for_club_match(self) -> None:
        club = Club.objects.create(
            name="Теннисный клуб Спартак",
            slug="spartak",
            city="Москва",
            address="ул. Пушкина, 1",
            email="club@test.local",
            admin_name="Администратор клуба",
        )
        tournament = Tournament.objects.create(
            name="Клубный турнир",
            slug="club-home-widget",
            city="Москва",
            club=club,
            start_date=timezone.now().date(),
            format="round_robin",
        )
        Match.objects.create(
            tournament=tournament,
            player1=self.player1,
            player2=self.player2,
            status=Match.MatchStatus.COMPLETED,
            completed_datetime=timezone.now() - timedelta(hours=1),
            winner=self.player1,
            player1_set1=6,
            player2_set1=4,
            player1_set2=6,
            player2_set2=3,
        )

        response = self.client.get(reverse("api_recent_matches"), secure=True)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["matches"][0]["club_name"], club.name)


class HomeUpcomingMatchesWidgetTestCase(TestCase):
    """Виджет предстоящих матчей на главной."""

    def setUp(self) -> None:
        self.client = Client()
        self.player1 = Player.objects.create(
            user=User.objects.create_user(
                email="home-upcoming-p1@test.local",
                password="testpass123",
                first_name="Кристина",
                last_name="Козубова",
            )
        )
        self.player2 = Player.objects.create(
            user=User.objects.create_user(
                email="home-upcoming-p2@test.local",
                password="testpass123",
                first_name="Вера",
                last_name="Фильмакова",
            )
        )

    def test_upcoming_matches_api_uses_deadline_when_scheduled_datetime_missing(
        self,
    ) -> None:
        """Турнирные матчи без scheduled_datetime должны попадать в виджет по deadline."""
        tournament = Tournament.objects.create(
            name="Круговой Воскресенск",
            slug="home-upcoming-rr",
            city="Воскресенск",
            start_date=timezone.now().date(),
            format="round_robin",
            status=TournamentStatus.ACTIVE,
        )
        match = Match.objects.create(
            tournament=tournament,
            player1=self.player1,
            player2=self.player2,
            status=Match.MatchStatus.SCHEDULED,
            scheduled_datetime=None,
            deadline=timezone.now() + timedelta(days=1),
        )

        response = self.client.get(reverse("api_upcoming_matches"), secure=True)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["matches"]), 1)
        self.assertEqual(payload["matches"][0]["id"], match.pk)
        self.assertIn("Козубова", payload["matches"][0]["player1"])


class HomeTopPlayersVisibilityTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        hidden_user = User.objects.create_user(
            email="hidden-home@test.local",
            password="testpass123",
            first_name="Скрытый",
            last_name="Игрок",
        )
        visible_user = User.objects.create_user(
            email="visible-home@test.local",
            password="testpass123",
            first_name="Видимый",
            last_name="Игрок",
        )
        Player.objects.create(
            user=hidden_user,
            total_points=9999.0,
            is_hidden_on_home=True,
        )
        Player.objects.create(
            user=visible_user,
            total_points=1000.0,
            is_hidden_on_home=False,
            is_verified=True,
        )

    def test_home_top_players_excludes_hidden_players(self) -> None:
        response = self.client.get(reverse("home"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Скрытый Игрок")
        self.assertContains(response, "Видимый Игрок")


class ClubDiscoverPageTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="footer-user@test.local",
            password="testpass123",
        )
        self.client.force_login(self.user)

    def test_club_discover_page_supports_filters_and_request_states(self) -> None:
        open_club = Club.objects.create(
            name="Открытый клуб",
            slug="open-club",
            city="Москва",
            address="ул. Первая, 1",
            email="open@test.local",
            admin_name="Админ клуба",
            description="Клуб для взрослых любителей",
        )
        pending_club = Club.objects.create(
            name="Клуб с заявкой",
            slug="pending-club",
            city="Казань",
            address="ул. Вторая, 2",
            email="pending@test.local",
            admin_name="Админ клуба",
            description="Клуб для турнирной подготовки",
        )
        ClubJoinRequest.objects.create(
            club=pending_club,
            user=self.user,
            status=ClubJoinRequestStatus.PENDING,
        )

        response = self.client.get(
            reverse("clubs:club_discover"),
            {"q": "клуб", "city": "Моск"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, open_club.name)
        self.assertNotContains(response, pending_club.name)
        self.assertContains(response, "Подать заявку")

        response = self.client.get(reverse("clubs:club_discover"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, open_club.name)
        self.assertContains(response, pending_club.name)
        self.assertContains(response, "Заявка отправлена")


class PlatformPlayersExportTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.staff_user = User.objects.create_user(
            email="staff@test.local",
            password="testpass123",
            is_staff=True,
        )
        self.client.force_login(self.staff_user)

    def test_platform_players_export_returns_csv_without_bye_players(self) -> None:
        user = User.objects.create_user(
            email="player-export@test.local",
            password="testpass123",
            first_name="Иван",
            last_name="Петров",
            phone="+79990000000",
        )
        Player.objects.create(
            user=user,
            city="Москва",
            gender="male",
            ntrp_level="3.5",
            skill_level="amateur",
            total_points=128.5,
            matches_played=14,
            matches_won=9,
        )
        bye_user = User.objects.create_user(
            email="bye-export@test.local",
            password="testpass123",
        )
        Player.objects.create(user=bye_user, is_bye=True)

        response = self.client.get(reverse("platform_players_export"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="platform_players.csv"',
        )

        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(
            rows[0],
            [
                "email",
                "first_name",
                "last_name",
                "phone",
                "city",
                "gender",
                "ntrp_level",
                "skill_level",
                "total_points",
                "matches_played",
                "matches_won",
                "created_at",
            ],
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "player-export@test.local")
        self.assertEqual(rows[1][1], "Иван")
        self.assertEqual(rows[1][2], "Петров")
        self.assertEqual(rows[1][3], "+79990000000")
        self.assertEqual(rows[1][4], "Москва")
        self.assertEqual(rows[1][5], "Мужской")


class HomeClubTournamentsIntegrationTestCase(TestCase):
    """Турниры клубов на главной, фильтр по клубу и CTA «Вступить в клуб»."""

    def setUp(self) -> None:
        self.client = Client()
        self.club_a = Club.objects.create(
            name="Клуб Альфа",
            slug="club-alpha",
            city="Москва",
            address="ул. Альфа, 1",
            email="alpha@test.local",
            admin_name="Админ",
        )
        self.club_b = Club.objects.create(
            name="Клуб Бета",
            slug="club-beta",
            city="Сочи",
            address="ул. Бета, 2",
            email="beta@test.local",
            admin_name="Админ",
        )
        self.platform_tournament = Tournament.objects.create(
            name="Турнир платформы",
            slug="home-platform-tm",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            status=TournamentStatus.UPCOMING,
            max_participants=32,
        )
        self.platform_tournament.allowed_categories.create(category="amateur")
        self.club_tournament = Tournament.objects.create(
            name="Внутриклубный кубок",
            slug="home-club-internal",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            status=TournamentStatus.UPCOMING,
            club=self.club_a,
            is_open_interclub=False,
            max_participants=16,
        )
        self.club_tournament.allowed_categories.create(category="amateur")
        self.interclub_tournament = Tournament.objects.create(
            name="Межклубный открытый",
            slug="home-interclub-open",
            city="Казань",
            start_date=date.today(),
            format="single_elimination",
            status=TournamentStatus.UPCOMING,
            club=self.club_a,
            is_open_interclub=True,
            max_participants=8,
        )
        self.interclub_tournament.allowed_categories.create(category="amateur")

        self.member_user = User.objects.create_user(
            email="member-alpha@test.local",
            password="pass12345",
            first_name="Мем",
            last_name="Бер",
            phone="+79990000001",
        )
        Player.objects.create(
            user=self.member_user,
            birth_date=date(1991, 5, 5),
            skill_level="amateur",
            gender="male",
        )
        ClubMember.objects.create(
            club=self.club_a,
            user=self.member_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )

        self.stranger_user = User.objects.create_user(
            email="stranger@test.local",
            password="pass12345",
            first_name="Чуж",
            last_name="Ак",
            phone="+79990000002",
        )
        Player.objects.create(
            user=self.stranger_user,
            birth_date=date(1992, 6, 6),
            skill_level="amateur",
            gender="male",
            is_verified=True,
        )

        self.beta_member_user = User.objects.create_user(
            email="member-beta@test.local",
            password="pass12345",
            first_name="Бета",
            last_name="Игрок",
            phone="+79990000003",
        )
        Player.objects.create(
            user=self.beta_member_user,
            birth_date=date(1993, 7, 7),
            skill_level="amateur",
            gender="male",
        )
        ClubMember.objects.create(
            club=self.club_b,
            user=self.beta_member_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )

    def test_home_lists_platform_and_internal_club_tournaments(self) -> None:
        response = self.client.get(reverse("home"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Турнир платформы")
        self.assertContains(response, "Внутриклубный кубок")
        self.assertContains(response, "Межклубный открытый")

    def test_home_club_filter_platform_only_excludes_club_rows(self) -> None:
        response = self.client.get(
            reverse("home"),
            {"club": "__platform__"},
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Турнир платформы")
        self.assertNotContains(response, "Внутриклубный кубок")

    def test_home_club_filter_club_only_excludes_platform_rows(self) -> None:
        response = self.client.get(
            reverse("home"),
            {"club": "__club_only__"},
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Внутриклубный кубок")
        self.assertNotContains(response, "Турнир платформы")

    def test_home_club_filter_by_slug(self) -> None:
        response = self.client.get(
            reverse("home"),
            {"club": "club-alpha"},
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Внутриклубный кубок")
        self.assertNotContains(response, "Турнир платформы")

    def test_club_tournament_detail_visible_to_anonymous(self) -> None:
        response = self.client.get(
            reverse("tournament_detail", kwargs={"slug": self.club_tournament.slug}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.club_tournament.name)

    def test_non_member_sees_join_club_on_detail(self) -> None:
        self.client.force_login(self.stranger_user)
        response = self.client.get(
            reverse("tournament_detail", kwargs={"slug": self.club_tournament.slug}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Вступить в клуб")

    def test_member_of_host_club_does_not_see_join_club_cta(self) -> None:
        self.client.force_login(self.member_user)
        response = self.client.get(
            reverse("tournament_detail", kwargs={"slug": self.club_tournament.slug}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Вступить в клуб")

    def test_member_of_other_club_sees_join_club_for_internal_tournament(self) -> None:
        self.client.force_login(self.beta_member_user)
        response = self.client.get(
            reverse("tournament_detail", kwargs={"slug": self.club_tournament.slug}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Вступить в клуб")

    def test_non_member_cannot_register_for_internal_club_tournament(self) -> None:
        self.client.force_login(self.stranger_user)
        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": self.club_tournament.slug}),
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.club_tournament.slug, response.url or "")

    def test_pending_join_request_shows_pending_state_on_detail(self) -> None:
        ClubJoinRequest.objects.create(
            club=self.club_a,
            user=self.stranger_user,
            status=ClubJoinRequestStatus.PENDING,
        )
        self.client.force_login(self.stranger_user)
        response = self.client.get(
            reverse("tournament_detail", kwargs={"slug": self.club_tournament.slug}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Заявка на вступление отправлена")

    def test_interclub_tournament_no_join_club_cta_for_stranger(self) -> None:
        self.client.force_login(self.stranger_user)
        response = self.client.get(
            reverse(
                "tournament_detail", kwargs={"slug": self.interclub_tournament.slug}
            ),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Вступить в клуб")


class SupportDialogsFlowTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.staff = User.objects.create_user(
            email="support-admin@test.local",
            password="pass12345",
            is_staff=True,
        )
        self.user = User.objects.create_user(
            email="support-user@test.local",
            password="pass12345",
        )

    def test_guest_feedback_requires_email(self) -> None:
        response = self.client.post(
            reverse("feedback_submit"),
            data='{"guest_name":"Гость","message":"Помогите"}',
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["error"].lower())

    def test_create_message_sends_email_and_unread_count(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("feedback_submit"),
            data='{"message":"Нужна помощь"}',
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        thread = SupportThread.objects.filter(user=self.user).first()
        self.assertIsNotNone(thread)
        assert thread is not None
        self.assertEqual(thread.admin_unread_count, 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_admin_reply_updates_user_unread_and_sends_mail(self) -> None:
        self.client.force_login(self.user)
        self.client.post(
            reverse("feedback_submit"),
            data='{"message":"Нужна помощь"}',
            content_type="application/json",
            secure=True,
        )
        thread = SupportThread.objects.get(user=self.user)

        from apps.core.support_notifications import send_user_support_reply

        self.client.force_login(self.staff)
        with patch(
            "apps.core.views.send_user_support_reply_async",
            side_effect=lambda message: send_user_support_reply(message),
        ):
            response = self.client.post(
                reverse("support_admin_reply"),
                data=f'{{"thread_id":{thread.id},"text":"Ответ поддержки"}}',
                content_type="application/json",
                secure=True,
            )
        self.assertEqual(response.status_code, 200)
        thread.refresh_from_db()
        self.assertEqual(thread.admin_unread_count, 0)
        self.assertEqual(thread.user_unread_count, 1)
        self.assertEqual(len(mail.outbox), 2)
