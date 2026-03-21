from datetime import timedelta

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
