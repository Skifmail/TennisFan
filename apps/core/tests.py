import csv
from datetime import timedelta
from io import StringIO

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import Club, ClubJoinRequest, ClubJoinRequestStatus
from apps.tournaments.models import Match, Tournament
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
