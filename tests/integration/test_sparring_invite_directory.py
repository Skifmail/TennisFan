"""Справочник игроков на странице приглашения на спарринг."""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.sparring.models import SparringInvitation
from apps.users.models import Player
from tests.support.factories import make_player, make_subscription, make_user


class SparringInviteDirectoryTests(TestCase):
    """Игроки платформы показываются по городам, начиная с города игрока."""

    def setUp(self) -> None:
        self.viewer_user = make_user(
            email="directory-viewer@test.local",
            first_name="Иван",
            last_name="Зритель",
        )
        self.viewer = Player.objects.create(
            user=self.viewer_user,
            city="Москва",
            is_verified=True,
            birth_date=date(1990, 1, 1),
        )
        self.viewer_user.phone = "+79990003344"
        self.viewer_user.save(update_fields=["phone"])
        make_subscription(
            self.viewer_user,
            tier_name="directory-silver",
            tier_kwargs={"has_sparring": True},
        )
        self.local_player = make_player(
            email_suffix="directory-local",
            first_name="Анна",
            last_name="Столичная",
        )
        self.local_player.city = "Москва"
        self.local_player.is_verified = True
        self.local_player.save(update_fields=["city", "is_verified"])
        self.zhukovsky_player = make_player(
            email_suffix="directory-zhuk",
            first_name="Пётр",
            last_name="Жук",
        )
        self.zhukovsky_player.city = "Жуковский"
        self.zhukovsky_player.is_verified = True
        self.zhukovsky_player.save(update_fields=["city", "is_verified"])
        self.kazan_player = make_player(
            email_suffix="directory-kazan",
            first_name="Борис",
            last_name="Казанский",
        )
        self.kazan_player.city = "Казань"
        self.kazan_player.is_verified = True
        self.kazan_player.save(update_fields=["city", "is_verified"])
        self.client.force_login(self.viewer_user)

    def test_view_switcher_marks_player_search_as_active(self) -> None:
        """На странице приглашения активна кнопка «Найти игрока»."""
        response = self.client.get(reverse("sparring_invite"), secure=True)
        html = response.content.decode()

        self.assertIn('class="sparring-tabs sparring-view-switch"', html)
        self.assertIn(
            f'href="{reverse("sparring_list")}" class="sparring-tab">'
            "Заявки на спарринг</a>",
            html,
        )
        self.assertIn(
            f'href="{reverse("sparring_invite")}" '
            'class="sparring-tab sparring-tab--active" '
            'aria-current="page">Найти игрока</a>',
            html,
        )

    def test_directory_starts_with_viewer_city_then_other_cities(self) -> None:
        """Сначала «Рядом с вами», затем другие города по алфавиту."""
        response = self.client.get(reverse("sparring_invite"), secure=True)
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Рядом с вами", html)
        self.assertIn("В других городах", html)
        self.assertLess(html.index("Рядом с вами"), html.index("Анна Столичная"))
        self.assertLess(html.index("Анна Столичная"), html.index("В других городах"))
        self.assertLess(html.index("Жуковский"), html.index("Казань"))
        self.assertLess(html.index("Пётр Жук"), html.index("Борис Казанский"))

    def test_directory_excludes_viewer_bye_and_inactive_players(self) -> None:
        """В списке нет самого игрока, служебного bye и отключённых аккаунтов."""
        bye = make_player(
            email_suffix="directory-bye", first_name="Свободный", last_name="Круг"
        )
        bye.city = "Москва"
        bye.is_bye = True
        bye.is_verified = True
        bye.save(update_fields=["city", "is_bye", "is_verified"])

        inactive_user = make_user(
            email="directory-inactive@test.local",
            first_name="Илья",
            last_name="Отключённый",
            is_active=False,
        )
        inactive = Player.objects.create(
            user=inactive_user, city="Москва", is_verified=True
        )
        self.assertFalse(inactive.user.is_active)

        unverified = make_player(
            email_suffix="directory-unverified",
            first_name="Ульяна",
            last_name="Безпочтовая",
        )
        unverified.city = "Москва"
        unverified.save(update_fields=["city"])

        response = self.client.get(reverse("sparring_invite"), secure=True)

        self.assertNotContains(response, "Иван Зритель")
        self.assertNotContains(response, "Свободный Круг")
        self.assertNotContains(response, "Илья Отключённый")
        self.assertNotContains(response, "Ульяна Безпочтовая")
        self.assertContains(response, "Анна Столичная")

    def test_pending_invitation_marks_player_as_already_invited(self) -> None:
        """Игроку с активным приглашением вместо кнопки показывается статус."""
        SparringInvitation.objects.create(
            inviter=self.viewer,
            invitee=self.local_player,
            is_friendly=False,
        )

        response = self.client.get(reverse("sparring_invite"), secure=True)

        self.assertContains(response, "Приглашение отправлено")
        self.assertNotContains(
            response,
            f'data-invite-id="{self.local_player.pk}"',
        )

    def test_search_results_replace_directory_until_reset(self) -> None:
        """Во время поиска показываются результаты, а не весь справочник."""
        response = self.client.get(
            reverse("sparring_invite"),
            {"q": "Жук"},
            secure=True,
        )

        self.assertContains(response, "Результаты поиска")
        self.assertContains(response, "Пётр Жук")
        self.assertNotContains(response, "В других городах")
        self.assertNotContains(response, "Борис Казанский")
        self.assertContains(response, "Сбросить поиск")

    def test_same_city_in_different_case_is_grouped_as_local(self) -> None:
        """Город в другом регистре не создаёт отдельную секцию."""
        lower_case = make_player(
            email_suffix="directory-lower",
            first_name="Мария",
            last_name="Нижний",
        )
        lower_case.city = "москва"
        lower_case.is_verified = True
        lower_case.save(update_fields=["city", "is_verified"])

        response = self.client.get(reverse("sparring_invite"), secure=True)
        html = response.content.decode()

        self.assertEqual(html.count("Рядом с вами"), 1)
        self.assertLess(html.index("Мария Нижний"), html.index("В других городах"))
