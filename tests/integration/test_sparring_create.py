"""Создание одиночной заявки на спарринг сохраняет её и уводит в «Мои заявки»."""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.sparring.models import SparringRequest
from apps.users.models import Player
from tests.support.factories import make_subscription, make_user


class SparringCreateViewTest(TestCase):
    """Заполненная одиночная заявка не должна просто перерисовывать форму."""

    def setUp(self) -> None:
        self.user = make_user(
            email="spar-create@test.local",
            first_name="Иван",
            last_name="Заявка",
            phone="+79990001144",
        )
        self.player = Player.objects.create(
            user=self.user,
            city="Сочи",
            is_verified=True,
            birth_date=date(1990, 1, 1),
        )
        make_subscription(
            self.user,
            tier_name="spar-create-silver",
            tier_kwargs={"has_sparring": True},
        )
        self.client.force_login(self.user)

    def test_filled_singles_form_creates_request(self) -> None:
        response = self.client.post(
            reverse("sparring_create") + "?type=singles&sport=tennis",
            {
                "sparring_type": "singles",
                "sport": "tennis",
                "city": "Сочи",
                "desired_category": "amateur",
                "preferred_gender": "open",
                "description": "Хочу играть вечером на выходных",
                "preferred_days": "Сб, Вс",
                "preferred_time": "19:00",
                "preferred_location": "Центральный корт",
                "desired_partner_age_min": "25",
                "desired_partner_age_max": "45",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/sparring/my/", response["Location"])
        created = SparringRequest.objects.get(player=self.player)
        self.assertEqual(created.city, "Сочи")
        self.assertEqual(created.description, "Хочу играть вечером на выходных")
        self.assertEqual(created.sport, "tennis")

    def test_missing_required_fields_stay_on_page_with_message(self) -> None:
        response = self.client.post(
            reverse("sparring_create"),
            {"sparring_type": "singles", "preferred_gender": "open"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Укажите населённый пункт.")
        self.assertContains(response, "Напишите описание: когда и где хотите играть.")
        self.assertFalse(SparringRequest.objects.filter(player=self.player).exists())
