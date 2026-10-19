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
        self.both_player = make_player(
            email_suffix="both", first_name="Олег", last_name="Два"
        )
        self.both_player.city = "Ramenskoe, Zhukovsky"
        self.both_player.save(update_fields=["city"])
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
        self.assertContains(response, "Заявки рядом")
        self.assertNotContains(response, "Анна Море")
        self.assertNotContains(response, "Далеко")
        self.assertNotContains(response, "Олег Два")
        invite_url = f"{reverse('sparring_invite')}?invitee={self.near_player.pk}"
        self.assertNotContains(response, invite_url)
        self.assertContains(response, "Радиус")
        self.assertContains(response, "25 км")
        self.assertContains(response, "10 км")
        self.assertNotContains(response, "Любое расстояние")

        players = self.client.get(
            reverse("sparring_list"),
            {"nearby": "25", "type": "singles", "nearby_list": "players"},
            secure=True,
        )
        self.assertContains(players, "Игроки рядом")
        self.assertContains(players, "Олег Два")
        self.assertContains(players, invite_url)
        self.assertNotContains(players, "Рядом")
        self.assertNotContains(players, "Далеко")
        self.assertNotContains(players, "Анна Море")

    def test_nearby_players_are_paged_nearest_first(self) -> None:
        """Все игроки в радиусе идут от ближних к дальним, по 20 на странице."""
        for index in range(19):
            player = make_player(
                email_suffix=f"near-page-{index}",
                first_name="Близкий",
                last_name=f"Ноль{index}",
            )
            player.city = "Ramenskoe"
            player.save(update_fields=["city"])

        first_page = self.client.get(
            reverse("sparring_list"),
            {"nearby": "25", "type": "singles", "nearby_list": "players"},
            secure=True,
        )
        self.assertEqual(first_page.status_code, 200)
        self.assertContains(first_page, "Олег Два")
        self.assertContains(first_page, "Страница 1 из 2")
        self.assertContains(first_page, "players_page=2")
        self.assertNotContains(
            first_page,
            f"{reverse('sparring_invite')}?invitee={self.near_player.pk}",
        )

        second_page = self.client.get(
            reverse("sparring_list"),
            {
                "nearby": "25",
                "type": "singles",
                "players_page": "2",
                "nearby_list": "players",
            },
            secure=True,
        )
        self.assertContains(second_page, "Пётр Жук")
        self.assertContains(second_page, "Страница 2 из 2")
        self.assertContains(second_page, "players_page=1")
        self.assertNotContains(second_page, "Олег Два")
        self.assertNotContains(second_page, "Анна Море")

    def test_profile_with_two_cities_matches_each_name(self) -> None:
        """Строка «Ramenskoe, Zhukovsky» находится по обоим городам в радиусе."""
        response = self.client.get(
            reverse("sparring_list"),
            {
                "nearby": "25",
                "type": "singles",
                "city": "Ramenskoe, Zhukovsky",
                "nearby_list": "players",
            },
            secure=True,
        )

        self.assertContains(response, "Олег Два")
        self.assertContains(response, "Пётр Жук")
        self.assertNotContains(response, "Анна Море")

    def test_profile_form_rejects_two_catalog_cities(self) -> None:
        """В профиле нельзя сохранить два города из справочника одной строкой."""
        from apps.core.localities import several_catalog_cities_message

        message = several_catalog_cities_message("Ramenskoe, Zhukovsky")
        self.assertIn("Ramenskoe", message)
        self.assertIn("Zhukovsky", message)
        self.assertEqual(
            several_catalog_cities_message("деревня Кимжа, Архангельская область"),
            "",
        )

    def test_radius_when_default_should_use_collapsed_menu_instead_of_full_row(
        self,
    ) -> None:
        """Радиусы доступны в свёрнутом меню рядом с полем города."""
        response = self.client.get(reverse("sparring_list"), secure=True)
        self.assertContains(response, '<details class="sparring-radius">')
        self.assertContains(response, 'aria-label="Радиус поиска"')
        self.assertContains(response, "nearby=10")
        self.assertContains(response, "nearby=100")
        self.assertContains(response, "Без ограничения")
        self.assertNotContains(response, "sparring-nearby-capsule")
        self.assertNotContains(response, 'sparring-radius" open')

    def test_radius_when_active_should_show_value_and_keep_reset_link(self) -> None:
        """Выбранный радиус заметен и снимается без потери остальных фильтров."""
        response = self.client.get(
            reverse("sparring_list"),
            {
                "nearby": "25",
                "type": "team",
                "preferred_gender": "male",
            },
            secure=True,
        )
        self.assertContains(
            response, '<details class="sparring-radius sparring-radius--active">'
        )
        self.assertContains(response, "25 км</summary>")
        self.assertContains(response, 'aria-current="true"')
        self.assertContains(
            response, '?sport=tennis&amp;type=team&amp;preferred_gender=male"'
        )

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
