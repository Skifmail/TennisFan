"""Заявка «Стать тренером»: подстановка профиля и виды спорта."""

from django.test import TestCase
from django.urls import reverse

from apps.core.sports import Sport, VenueSport
from apps.training.forms import CoachApplicationForm, TrainingForm
from apps.training.models import Coach, CoachApplication, Training
from tests.support.factories import make_player, make_user


class CoachApplicationPrefillTestCase(TestCase):
    """Известные данные игрока подставляются в форму заявки."""

    def setUp(self) -> None:
        self.player = make_player(
            email_suffix="become-coach",
            first_name="Александр",
            last_name="Штайло",
            phone="+79001112233",
        )
        self.player.city = "Казань"
        self.player.telegram = "@shtaylo"
        self.player.whatsapp = "79001112233"
        self.player.max_contact = "https://max.ru/shtaylo"
        self.player.save(update_fields=["city", "telegram", "whatsapp", "max_contact"])
        self.client.force_login(self.player.user)

    def test_get_form_prefills_profile_fields(self) -> None:
        response = self.client.get(reverse("coach_application_create"), secure=True)

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial["applicant_name"], "Александр Штайло")
        self.assertEqual(form.initial["applicant_email"], self.player.user.email)
        self.assertEqual(form.initial["applicant_phone"], "+79001112233")
        self.assertEqual(form.initial["name"], "Александр Штайло")
        self.assertEqual(form.initial["phone"], "+79001112233")
        self.assertEqual(form.initial["telegram"], "@shtaylo")
        self.assertEqual(form.initial["city"], "Казань")
        self.assertContains(response, 'name="sports"')
        self.assertContains(response, 'value="padel"')

    def test_submit_both_sports_and_approve(self) -> None:
        response = self.client.post(
            reverse("coach_application_create"),
            {
                "applicant_name": "Александр Штайло",
                "applicant_email": self.player.user.email,
                "applicant_phone": "+79001112233",
                "name": "Александр Штайло",
                "experience_years": "5",
                "city": "Казань",
                "sports": ["tennis", "padel"],
                "phone": "+79001112233",
                "telegram": "@shtaylo",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        application = CoachApplication.objects.get(applicant_user=self.player.user)
        self.assertEqual(application.sport, VenueSport.BOTH)
        self.assertEqual(application.city, "Казань")
        self.assertEqual(application.phone, "+79001112233")

        coach = application.approve_and_create_coach()
        self.assertEqual(coach.sport, VenueSport.BOTH)
        self.assertEqual(coach.name, "Александр Штайло")
        self.assertEqual(coach.sports_display, ["Теннис", "Падел"])


class CoachApplicationFormSportsTestCase(TestCase):
    """Без выбранного вида спорта заявка не проходит."""

    def test_requires_at_least_one_sport(self) -> None:
        form = CoachApplicationForm(
            data={
                "applicant_name": "Иван",
                "applicant_email": "ivan@test.local",
                "name": "Иван",
                "experience_years": "1",
                "city": "Москва",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("sports", form.errors)


class TrainingFormSportsTestCase(TestCase):
    """При создании тренировки можно выбрать падел или оба вида."""

    def test_saves_padel(self) -> None:
        form = TrainingForm(
            data={
                "title": "Падел группа",
                "description": "Описание тренировки",
                "type_prices_checked_group": "on",
                "type_prices_price_group": "2000",
                "skill_levels": ["amateur"],
                "city": "Казань",
                "duration_minutes": "60",
                "max_participants": "4",
                "sports": ["padel"],
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        training = form.save(commit=False)
        training.slug = "padel-group-form"
        training.save()
        self.assertEqual(training.sport, Sport.PADEL)

    def test_saves_both(self) -> None:
        form = TrainingForm(
            data={
                "title": "Теннис и падел",
                "description": "Описание",
                "type_prices_checked_individual": "on",
                "type_prices_price_individual": "3000",
                "skill_levels": ["novice"],
                "city": "Москва",
                "duration_minutes": "60",
                "max_participants": "1",
                "sports": ["tennis", "padel"],
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        training = form.save(commit=False)
        training.slug = "both-sports-form"
        training.save()
        self.assertEqual(training.sport, VenueSport.BOTH)


class TrainingListSportFilterTestCase(TestCase):
    """Тренировка «оба вида» видна в теннисе и паделе."""

    def setUp(self) -> None:
        self.both = Training.objects.create(
            title="Универсальная",
            slug="universal-both",
            description="Описание",
            city="Москва",
            sport=VenueSport.BOTH,
            is_active=True,
            type_prices={"group": 1500},
        )
        self.padel = Training.objects.create(
            title="Только падел",
            slug="padel-only-list",
            description="Описание",
            city="Москва",
            sport=Sport.PADEL,
            is_active=True,
            type_prices={"group": 1500},
        )
        self.tennis = Training.objects.create(
            title="Только теннис",
            slug="tennis-only-list",
            description="Описание",
            city="Москва",
            sport=Sport.TENNIS,
            is_active=True,
            type_prices={"group": 1500},
        )

    def test_padel_filter_includes_both(self) -> None:
        response = self.client.get(
            reverse("training_list"),
            {"sport": "padel"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        titles = [item.title for item in response.context["trainings"]]
        self.assertIn("Универсальная", titles)
        self.assertIn("Только падел", titles)
        self.assertNotIn("Только теннис", titles)

    def test_tennis_filter_includes_both(self) -> None:
        response = self.client.get(
            reverse("training_list"),
            {"sport": "tennis"},
            secure=True,
        )

        titles = [item.title for item in response.context["trainings"]]
        self.assertIn("Универсальная", titles)
        self.assertIn("Только теннис", titles)
        self.assertNotIn("Только падел", titles)


class CoachPublicSportsTestCase(TestCase):
    """Вид спорта виден на публичной странице тренера."""

    def setUp(self) -> None:
        self.user = make_user(email="viewer-sports@test.local")
        self.client.force_login(self.user)
        self.coach = Coach.objects.create(
            name="Леонид Ермолаев",
            slug="leonid-sports",
            city="Москва",
            sport=VenueSport.BOTH,
            is_active=True,
        )

    def test_detail_shows_tennis_and_padel(self) -> None:
        response = self.client.get(
            reverse("coach_detail", kwargs={"slug": self.coach.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Теннис")
        self.assertContains(response, "Падел")

    def test_list_shows_sports(self) -> None:
        response = self.client.get(reverse("coach_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Теннис")
        self.assertContains(response, "Падел")


class CoachListSportSwitcherTestCase(TestCase):
    """Список тренеров фильтруется тумблером теннис/падел."""

    def setUp(self) -> None:
        self.user = make_user(email="coach-list-sport@test.local")
        self.client.force_login(self.user)
        Coach.objects.create(
            name="Теннисный тренер",
            slug="tennis-coach-list",
            city="Москва",
            sport=VenueSport.TENNIS,
            is_active=True,
        )
        Coach.objects.create(
            name="Падел тренер",
            slug="padel-coach-list",
            city="Москва",
            sport=VenueSport.PADEL,
            is_active=True,
        )
        Coach.objects.create(
            name="Универсальный тренер",
            slug="both-coach-list",
            city="Москва",
            sport=VenueSport.BOTH,
            is_active=True,
        )

    def test_default_shows_tennis_coaches_and_switcher(self) -> None:
        response = self.client.get(reverse("coach_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        names = [coach.name for coach in response.context["coaches"]]
        self.assertIn("Теннисный тренер", names)
        self.assertIn("Универсальный тренер", names)
        self.assertNotIn("Падел тренер", names)
        self.assertContains(response, "profile-sport-switch__track")
        self.assertContains(response, 'aria-label="Вид спорта тренеров"')
        self.assertContains(response, 'data-sport-swap="coaches"')
        self.assertContains(response, "js/sport_switch.js")
        self.assertNotContains(response, "profile-sport-switch__track is-padel")
        self.assertContains(response, ">Наши тренеры</h1>")
        self.assertNotContains(response, ">Наши тренеры — падел</h1>")

    def test_padel_shows_padel_coaches(self) -> None:
        response = self.client.get(
            reverse("coach_list"),
            {"sport": "padel"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        names = [coach.name for coach in response.context["coaches"]]
        self.assertIn("Падел тренер", names)
        self.assertIn("Универсальный тренер", names)
        self.assertNotIn("Теннисный тренер", names)
        self.assertContains(response, "profile-sport-switch__track is-padel")
        self.assertContains(response, "Наши тренеры — падел")
        self.assertEqual(response.context["current_sport"], Sport.PADEL)
