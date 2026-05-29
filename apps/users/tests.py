from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from apps.clubs.models import Club
from apps.tournaments.models import Match, Tournament
from apps.users.models import Player, User


class GlobalProfileIsolationTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="player@test.local",
            password="testpass123",
            first_name="Леонид",
            last_name="Ермолаев",
        )
        self.player = Player.objects.create(user=self.user)
        self.opponent = Player.objects.create(
            user=User.objects.create_user(
                email="opponent@test.local",
                password="testpass123",
                first_name="Александр",
                last_name="Шатайло",
            )
        )
        self.client.force_login(self.user)

    def test_global_profile_hides_club_tournament_matches(self) -> None:
        global_tournament = Tournament.objects.create(
            name="Глобальный турнир",
            slug="global-profile-visible",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        club = Club.objects.create(
            name="Клуб",
            slug="club-profile-hidden",
            city="Москва",
            address="ул. Пушкина, 1",
            email="club@test.local",
            admin_name="Администратор клуба",
        )
        club_tournament = Tournament.objects.create(
            name="Клубный турнир",
            slug="club-profile-hidden-tournament",
            city="Москва",
            club=club,
            start_date=date.today(),
            format="single_elimination",
        )

        Match.objects.create(
            tournament=global_tournament,
            player1=self.player,
            player2=self.opponent,
            status=Match.MatchStatus.SCHEDULED,
        )
        Match.objects.create(
            tournament=club_tournament,
            player1=self.player,
            player2=self.opponent,
            status=Match.MatchStatus.SCHEDULED,
        )

        response = self.client.get(
            reverse("profile", kwargs={"pk": self.player.pk}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Глобальный турнир")
        self.assertNotContains(response, "Клубный турнир")


class AuthEmailCaseInsensitiveLoginTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.password = "testpass123"
        self.user = User.objects.create_user(
            email="tennis@tennisfan.ru",
            password=self.password,
            first_name="Тест",
            last_name="Пользователь",
        )

    def test_login_with_mixed_case_email_works(self) -> None:
        response = self.client.post(
            reverse("auth"),
            data={"username": "Tennis@tennisfan.ru", "password": self.password},
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))
        self.assertEqual(
            str(self.client.session.get("_auth_user_id")), str(self.user.pk)
        )


class DisplayNameFormatTestCase(TestCase):
    """Единый формат отображения имён: Имя Фамилия."""

    def test_user_and_player_display_name(self) -> None:
        user = User.objects.create_user(
            email="name@test.local",
            password="x",
            first_name="Кристина",
            last_name="Козубова",
        )
        player = Player.objects.create(user=user)

        self.assertEqual(user.get_display_name(), "Кристина Козубова")
        self.assertEqual(player.get_display_name(), "Кристина Козубова")
        self.assertEqual(str(player), "Кристина Козубова")
