"""Поиск спарринг-заявок и игроков в радиусе населённого пункта."""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.core.models import City
from apps.sparring.models import SparringInvitation, SparringRequest
from apps.users.models import Player
from tests.support.factories import make_player, make_subscription, make_user


class NearbySparringTests(TestCase):
    """Радиус показывает близкие заявки и игроков и ведёт к приглашению."""

    def setUp(self) -> None:
        City.objects.create(name="Ramenskoe", lat=55.567, lng=38.230)
        City.objects.create(name="Zhukovsky", lat=55.595, lng=38.120)
        City.objects.create(name="Sochi", lat=43.585, lng=39.723)
        self.viewer_user = make_user(
            email="near-viewer@test.local",
            first_name="Иван",
            last_name="Рядом",
        )
        self.viewer = Player.objects.create(
            user=self.viewer_user,
            city="Ramenskoe",
            is_verified=True,
            birth_date=date(1990, 1, 1),
        )
        self.viewer_user.phone = "+79990001122"
        self.viewer_user.save(update_fields=["phone"])
        make_subscription(
            self.viewer_user,
            tier_name="nearby-silver",
            tier_kwargs={"has_sparring": True},
        )
        self.near_player = make_player(
            email_suffix="zhuk", first_name="Пётр", last_name="Жук"
        )
        self.near_player.city = "Zhukovsky"
        self.near_player.save(update_fields=["city"])
        self.far_player = make_player(
            email_suffix="sochi", first_name="Анна", last_name="Море"
        )
        self.far_player.city = "Sochi"
        self.far_player.save(update_fields=["city"])
        SparringRequest.objects.create(
            player=self.near_player,
            city="Zhukovsky",
            description="Рядом",
        )
        SparringRequest.objects.create(
            player=self.far_player,
            city="Sochi",
            description="Далеко",
        )
        self.client.force_login(self.viewer_user)

    def test_radius_shows_nearby_request_and_player_with_invite_link(self) -> None:
        """В радиусе 25 км есть Жуковский и нет Сочи, у игрока есть приглашение."""
        response = self.client.get(
            reverse("sparring_list"),
            {"nearby": "25", "type": "singles"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Пётр Жук")
        self.assertContains(response, "Рядом")
        self.assertNotContains(response, "Анна Море")
        self.assertNotContains(response, "Далеко")
        invite_url = f"{reverse('sparring_invite')}?invitee={self.near_player.pk}"
        self.assertContains(response, invite_url)
        self.assertContains(response, "Поиск рядом")
        self.assertContains(response, "25 км")
        self.assertContains(response, "10 км")
        self.assertNotContains(response, "Любое расстояние")

    def test_nearby_button_hides_radius_until_clicked(self) -> None:
        """До нажатия «Поиск рядом» кнопок расстояния нет."""
        response = self.client.get(reverse("sparring_list"), secure=True)

        self.assertContains(response, "Поиск рядом")
        self.assertContains(response, "nearby=25")
        self.assertNotContains(response, "10 км")
        self.assertNotContains(response, "Любое расстояние")
        self.assertNotContains(response, "Игроки рядом")

    def test_invite_page_opens_selected_nearby_player(self) -> None:
        """Клик по игроку открывает форму приглашения именно ему."""
        response = self.client.get(
            reverse("sparring_invite"),
            {"invitee": self.near_player.pk},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Пётр Жук")
        self.assertContains(
            response, f'name="invitee_id" value="{self.near_player.pk}"'
        )

    def test_invite_from_nearby_player_is_sent(self) -> None:
        """Отправка формы создаёт приглашение выбранному игроку."""
        response = self.client.post(
            reverse("sparring_invite"),
            {"invitee_id": self.near_player.pk},
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("sparring_my_invitations"),
            fetch_redirect_response=False,
        )
        self.assertTrue(
            SparringInvitation.objects.filter(
                inviter=self.viewer,
                invitee=self.near_player,
            ).exists()
        )
