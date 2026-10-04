"""Подстановка данных игрока при создании тренера в админке."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.training.coach_defaults import (
    apply_coach_defaults,
    coach_defaults_from_user,
)
from apps.training.forms import CoachAdminForm
from tests.support.factories import make_player

User = get_user_model()


class CoachDefaultsFromUserTestCase(TestCase):
    """ФИО, контакты и город берутся из профиля игрока."""

    def setUp(self) -> None:
        self.player = make_player(
            email_suffix="coach-src",
            first_name="Александр",
            last_name="Штайло",
            phone="+79001112233",
        )
        self.player.city = "Казань"
        self.player.telegram = "@shtaylo"
        self.player.whatsapp = "79001112233"
        self.player.max_contact = "https://max.ru/shtaylo"
        self.player.save(update_fields=["city", "telegram", "whatsapp", "max_contact"])

    def test_collects_known_profile_fields(self) -> None:
        defaults = coach_defaults_from_user(self.player.user)

        self.assertEqual(defaults["name"], "Александр Штайло")
        self.assertEqual(defaults["phone"], "+79001112233")
        self.assertEqual(defaults["telegram"], "@shtaylo")
        self.assertEqual(defaults["whatsapp"], "79001112233")
        self.assertEqual(defaults["max_contact"], "https://max.ru/shtaylo")
        self.assertEqual(defaults["city"], "Казань")

    def test_does_not_use_email_as_coach_name(self) -> None:
        user = User.objects.create_user(
            email="noname@test.local",
            password="testpass123",
        )

        defaults = coach_defaults_from_user(user)

        self.assertEqual(defaults["name"], "")

    def test_keeps_manually_entered_name(self) -> None:
        filled = apply_coach_defaults(
            {"name": "Другое имя", "city": "", "phone": ""},
            coach_defaults_from_user(self.player.user),
        )

        self.assertEqual(filled["name"], "Другое имя")
        self.assertEqual(filled["city"], "Казань")
        self.assertEqual(filled["phone"], "+79001112233")


class CoachAdminFormDefaultsTestCase(TestCase):
    """Админ-форма подставляет пустые поля из выбранного пользователя."""

    def setUp(self) -> None:
        self.player = make_player(
            email_suffix="coach-form",
            first_name="Леонид",
            last_name="Ермолаев",
            phone="+79005550011",
        )
        self.player.city = "Москва"
        self.player.telegram = "@ermolaev"
        self.player.save(update_fields=["city", "telegram"])

    def test_blank_fields_are_filled_from_player(self) -> None:
        form = CoachAdminForm(
            data={
                "user": str(self.player.user_id),
                "name": "",
                "slug": "",
                "city": "",
                "experience_years": "0",
                "is_active": "on",
                "sports": ["tennis"],
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        coach = form.save()
        self.assertEqual(coach.name, "Леонид Ермолаев")
        self.assertEqual(coach.city, "Москва")
        self.assertEqual(coach.phone, "+79005550011")
        self.assertEqual(coach.telegram, "@ermolaev")
        self.assertTrue(coach.slug)

    def test_typed_values_are_kept(self) -> None:
        form = CoachAdminForm(
            data={
                "user": str(self.player.user_id),
                "name": "Тренер вручную",
                "slug": "trener-vruchnuyu",
                "city": "Сочи",
                "phone": "+79990001122",
                "experience_years": "0",
                "is_active": "on",
                "sports": ["tennis", "padel"],
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        coach = form.save()
        self.assertEqual(coach.name, "Тренер вручную")
        self.assertEqual(coach.city, "Сочи")
        self.assertEqual(coach.phone, "+79990001122")
        self.assertEqual(coach.sport, "both")


class CoachAdminUserDefaultsViewTestCase(TestCase):
    """JSON для автозаполнения формы тренера в админке."""

    def setUp(self) -> None:
        self.admin = User.objects.create_superuser(
            email="admin-coach@test.local",
            password="testpass123",
        )
        self.client.force_login(self.admin)
        self.player = make_player(
            email_suffix="coach-json",
            first_name="Иван",
            last_name="Петров",
            phone="+79001230000",
        )
        self.player.city = "Санкт-Петербург"
        self.player.save(update_fields=["city"])

    def test_staff_receives_defaults(self) -> None:
        url = reverse(
            "admin:training_coach_user_defaults",
            args=[self.player.user_id],
        )
        response = self.client.get(url, secure=True)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["name"], "Иван Петров")
        self.assertEqual(payload["city"], "Санкт-Петербург")
        self.assertEqual(payload["phone"], "+79001230000")

    def test_anonymous_is_redirected(self) -> None:
        self.client.logout()
        url = reverse(
            "admin:training_coach_user_defaults",
            args=[self.player.user_id],
        )
        response = self.client.get(url, secure=True)

        self.assertEqual(response.status_code, 302)
