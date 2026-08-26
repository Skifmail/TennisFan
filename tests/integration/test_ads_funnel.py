"""Интеграционные тесты пути рекламного трафика: карточка → вход → запись.

Покрывают решения фазы 10: CTA «Принять участие» для анонима, сквозной `next`,
запись на турнир без ручной верификации и автоверификацию игрока.
"""

from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from apps.tournaments.models import Tournament, TournamentStatus
from apps.users.models import Player, User
from apps.users.verification import (
    get_missing_profile_fields,
    profile_is_filled,
    try_auto_verify,
)


def create_tournament(slug: str, *, variant: str = "singles") -> Tournament:
    """Создать предстоящий турнир с открытой регистрацией.

    Args:
        slug: Слаг турнира.
        variant: Вариант турнира — `singles` или `doubles`.

    Returns:
        Tournament: Созданный турнир с разрешённой категорией «amateur».
    """
    tournament = Tournament.objects.create(
        name=f"Турнир {slug}",
        slug=slug,
        city="Москва",
        start_date=date.today(),
        format="round_robin",
        status=TournamentStatus.UPCOMING,
        gender="open",
        is_one_day=True,
        entry_fee=0,
        variant=variant,
    )
    tournament.allowed_categories.create(category="amateur")
    return tournament


class AutoVerificationTestCase(TestCase):
    """Автоверификация при заполненном профиле и подтверждённом email."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="auto-verify@test.local",
            password="testpass123",
            first_name="Пётр",
            last_name="Иванов",
            phone="+79990000001",
        )
        self.user.email_verified = False
        self.user.save(update_fields=["email_verified"])
        self.player = Player.objects.create(
            user=self.user,
            skill_level="amateur",
            birth_date=date(1990, 1, 1),
            is_verified=False,
        )

    def test_verifies_when_profile_filled_and_email_confirmed(self) -> None:
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])

        self.assertTrue(try_auto_verify(self.player))
        self.player.refresh_from_db()
        self.assertTrue(self.player.is_verified)

    def test_skips_when_email_not_confirmed(self) -> None:
        self.assertFalse(try_auto_verify(self.player))
        self.player.refresh_from_db()
        self.assertFalse(self.player.is_verified)

    def test_skips_when_profile_incomplete(self) -> None:
        self.user.email_verified = True
        self.user.phone = ""
        self.user.save(update_fields=["email_verified", "phone"])

        self.assertFalse(try_auto_verify(self.player))
        self.player.refresh_from_db()
        self.assertFalse(self.player.is_verified)

    def test_is_idempotent_for_already_verified_player(self) -> None:
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])
        self.player.is_verified = True
        self.player.save(update_fields=["is_verified"])

        self.assertFalse(try_auto_verify(self.player))

    def test_handles_missing_player(self) -> None:
        self.assertFalse(try_auto_verify(None))

    def test_missing_fields_are_reported(self) -> None:
        self.user.phone = ""
        self.assertEqual(get_missing_profile_fields(self.user, self.player), ["phone"])
        self.assertFalse(profile_is_filled(self.user, self.player))

    def test_profile_save_verifies_player(self) -> None:
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])
        client = Client()
        client.force_login(self.user)

        response = client.post(
            reverse("profile_edit"),
            {
                "first_name": "Пётр",
                "last_name": "Иванов",
                "phone": "+79990000001",
                "birth_date": "1990-01-01",
                "city": "Москва",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.player.refresh_from_db()
        self.assertTrue(self.player.is_verified)


class AnonymousTournamentCtaTestCase(TestCase):
    """CTA на карточке турнира для неавторизованного посетителя."""

    def setUp(self) -> None:
        self.client = Client()
        self.singles = create_tournament("cta-singles")
        self.doubles = create_tournament("cta-doubles", variant="doubles")

    def test_card_offers_join_instead_of_login(self) -> None:
        response = self.client.get(reverse("tournament_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Принять участие", body)
        # Старое поведение вело анонима на вход и теряло выбранный турнир.
        login_to_catalog = f"{reverse('login')}?next={reverse('tournament_list')}"
        self.assertNotIn(login_to_catalog, body)

    def test_card_links_to_registration_by_variant(self) -> None:
        response = self.client.get(reverse("tournament_list"), secure=True)
        body = response.content.decode()

        self.assertIn(
            reverse("tournament_register", kwargs={"slug": self.singles.slug}), body
        )
        self.assertIn(
            reverse("tournament_register_doubles", kwargs={"slug": self.doubles.slug}),
            body,
        )

    def test_card_is_a_real_link_to_detail_page(self) -> None:
        response = self.client.get(reverse("tournament_list"), secure=True)
        detail_url = reverse("tournament_detail", kwargs={"slug": self.singles.slug})

        self.assertContains(response, f'href="{detail_url}"')


class RegistrationEntryPointTestCase(TestCase):
    """Вход в регистрацию: сохранение цели и отсутствие ручной верификации."""

    def setUp(self) -> None:
        self.client = Client()
        self.tournament = create_tournament("funnel-singles")
        self.register_url = reverse(
            "tournament_register", kwargs={"slug": self.tournament.slug}
        )

    def test_anonymous_is_sent_to_login_keeping_the_tournament(self) -> None:
        response = self.client.get(self.register_url, secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertIn(self.register_url, response.url)

    def test_login_returns_to_the_selected_tournament(self) -> None:
        user = User.objects.create_user(
            email="funnel-login@test.local",
            password="testpass123",
            first_name="Анна",
            last_name="Смирнова",
            phone="+79990000002",
        )
        Player.objects.create(
            user=user,
            skill_level="amateur",
            birth_date=date(1990, 1, 1),
        )

        response = self.client.post(
            reverse("auth"),
            {
                "form_type": "login",
                "username": user.email,
                "password": "testpass123",
                "next": self.register_url,
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.register_url)

    def test_unverified_player_is_not_bounced_to_profile(self) -> None:
        user = User.objects.create_user(
            email="funnel-unverified@test.local",
            password="testpass123",
            first_name="Игорь",
            last_name="Петров",
            phone="+79990000003",
        )
        Player.objects.create(
            user=user,
            skill_level="amateur",
            birth_date=date(1990, 1, 1),
            is_verified=False,
        )
        self.client.force_login(user)

        response = self.client.get(self.register_url, secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url, reverse("profile_edit"))


class PendingStartDateTestCase(TestCase):
    """Показ условия старта вместо технического годового диапазона дат."""

    def setUp(self) -> None:
        self.client = Client()
        self.tournament = create_tournament("dates-pending")
        self.tournament.end_date = date(
            self.tournament.start_date.year + 1,
            self.tournament.start_date.month,
            self.tournament.start_date.day,
        )
        self.tournament.save(update_fields=["end_date"])

    def test_pending_until_bracket_is_generated(self) -> None:
        self.assertTrue(self.tournament.start_date_is_pending)

        self.tournament.bracket_generated = True
        self.assertFalse(self.tournament.start_date_is_pending)

    def test_finished_tournament_shows_real_dates(self) -> None:
        self.tournament.status = TournamentStatus.COMPLETED
        self.assertFalse(self.tournament.start_date_is_pending)

    def test_card_hides_year_long_range(self) -> None:
        response = self.client.get(reverse("tournament_list"), secure=True)

        self.assertContains(response, "Старт после набора группы")
        self.assertNotContains(response, self.tournament.end_date.strftime("%d.%m.%Y"))

    def test_detail_page_hides_year_long_range(self) -> None:
        response = self.client.get(
            reverse("tournament_detail", kwargs={"slug": self.tournament.slug}),
            secure=True,
        )

        self.assertContains(response, "старт после набора группы")
        self.assertNotContains(response, self.tournament.end_date.strftime("%d.%m.%Y"))

    def test_detail_page_shows_dates_after_bracket(self) -> None:
        self.tournament.bracket_generated = True
        self.tournament.save(update_fields=["bracket_generated"])

        response = self.client.get(
            reverse("tournament_detail", kwargs={"slug": self.tournament.slug}),
            secure=True,
        )

        self.assertContains(response, self.tournament.start_date.strftime("%d.%m.%Y"))
        self.assertNotContains(response, "старт после набора группы")
