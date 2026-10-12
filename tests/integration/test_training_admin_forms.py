"""Страницы админки тренеров, заявок и тренировок открываются и сохраняют вид спорта."""

from django.test import TestCase
from django.urls import reverse

from apps.core.sports import VenueSport
from apps.training.models import Coach, CoachApplication, Training
from tests.support.factories import make_user


class TrainingAdminFormsTests(TestCase):
    """Регрессия: поле ``sports`` видно Django admin до ``__init__`` формы."""

    def setUp(self) -> None:
        self.admin = make_user(
            email="training-admin@test.local", is_staff=True, is_superuser=True
        )
        self.coach = Coach.objects.create(name="Тренер", slug="trener", city="Москва")
        self.training = Training.objects.create(
            title="Группа",
            slug="gruppa",
            description="Описание",
            city="Москва",
            coach=self.coach,
            type_prices={"group": 2000},
        )
        self.application = CoachApplication.objects.create(
            applicant_name="Заявитель",
            applicant_email="applicant@test.local",
            name="Заявитель",
            city="Москва",
        )
        self.client.force_login(self.admin)

    def test_add_and_change_pages_render(self) -> None:
        """Страницы добавления и изменения не падают."""
        urls = [
            reverse("admin:training_coach_add"),
            reverse("admin:training_coach_change", args=[self.coach.pk]),
            reverse("admin:training_coachapplication_add"),
            reverse(
                "admin:training_coachapplication_change", args=[self.application.pk]
            ),
            reverse("admin:training_training_add"),
            reverse("admin:training_training_change", args=[self.training.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url, secure=True)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'name="sports"')

    def test_admin_creates_coach_with_sports(self) -> None:
        """Тренер из админки сохраняется с выбранными видами спорта."""
        response = self.client.post(
            reverse("admin:training_coach_add"),
            {
                "name": "Новый тренер",
                "slug": "novyi-trener",
                "city": "Москва",
                "experience_years": "3",
                "is_active": "on",
                "sports": ["tennis", "padel"],
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        coach = Coach.objects.get(slug="novyi-trener")
        self.assertEqual(coach.sport, VenueSport.BOTH)

    def test_admin_creates_training_with_sports(self) -> None:
        """Тренировка из админки сохраняется с выбранным видом спорта."""
        response = self.client.post(
            reverse("admin:training_training_add"),
            {
                "title": "Падел утром",
                "slug": "padel-utrom",
                "sports": ["padel"],
                "description": "Описание",
                "type_prices_checked_group": "on",
                "type_prices_price_group": "1500",
                "skill_levels": ["novice"],
                "coach": str(self.coach.pk),
                "city": "Москва",
                "duration_minutes": "60",
                "max_participants": "4",
                "is_active": "on",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        training = Training.objects.get(slug="padel-utrom")
        self.assertEqual(training.sport, VenueSport.PADEL)

    def test_admin_invalid_training_is_redisplayed(self) -> None:
        """Ошибка в форме тренировки показывается, а не даёт 500."""
        response = self.client.post(
            reverse("admin:training_training_add"),
            {"title": "Без типа", "slug": "bez-tipa", "sports": ["tennis"]},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("type_prices", response.context["adminform"].form.errors)


class CoachTrainingFormRedisplayTests(TestCase):
    """Сайтовая форма тренировки корректно показывается повторно с ошибками."""

    def setUp(self) -> None:
        self.coach_user = make_user(email="form-coach@test.local")
        Coach.objects.create(
            user=self.coach_user, name="Тренер", slug="form-coach", city="Москва"
        )
        self.client.force_login(self.coach_user)

    def test_missing_type_shows_error(self) -> None:
        """Без выбранного типа тренировки форма возвращается с ошибкой."""
        response = self.client.post(
            reverse("training_add"),
            {
                "title": "Без типа",
                "description": "Описание",
                "sports": ["tennis"],
                "skill_levels": ["novice"],
                "city": "Москва",
                "duration_minutes": "60",
                "max_participants": "1",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="type_prices_price_group"')
        self.assertContains(response, 'class="text-error"')
        self.assertFalse(Training.objects.filter(title="Без типа").exists())

    def test_entered_prices_survive_other_errors(self) -> None:
        """Введённые цены сохраняются в форме, если ошибка в другом поле."""
        response = self.client.post(
            reverse("training_add"),
            {
                "title": "",
                "description": "Описание",
                "sports": ["tennis"],
                "skill_levels": ["novice"],
                "type_prices_checked_group": "on",
                "type_prices_price_group": "1500",
                "city": "Москва",
                "duration_minutes": "60",
                "max_participants": "1",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="type_prices_price_group"')
        self.assertContains(response, 'value="1500.0"')
