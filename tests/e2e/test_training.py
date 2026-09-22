"""E2E: публичный раздел тренировок и тренеров."""

from django.test import TestCase
from django.urls import reverse

from apps.training.models import Coach, Training, TrainingEnrollment
from apps.users.models import User
from tests.support.factories import make_player, make_user


class CoachVisibilityTests(TestCase):
    """Проверки видимости тренеров в публичном разделе."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="viewer@test.local",
            password="testpass123",
        )
        self.client.force_login(self.user)

    def test_inactive_coach_with_active_training_is_shown_in_coach_list(self) -> None:
        """Тренер с активной тренировкой должен отображаться в списке."""
        coach = Coach.objects.create(
            name="Леонид Ермолаев",
            slug="leonid-ermolaev",
            city="Москва",
            is_active=False,
        )
        Training.objects.create(
            title="Тестовая тренировка",
            slug="test-training",
            description="Описание",
            city="Москва",
            coach=coach,
            is_active=True,
            type_prices={"individual": 3000},
        )

        response = self.client.get(reverse("coach_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Леонид Ермолаев")

    def test_inactive_coach_with_active_training_is_opened_by_slug(self) -> None:
        """Карточка тренера из активной тренировки не должна вести в 404."""
        coach = Coach.objects.create(
            name="Леонид Ермолаев",
            slug="leonid-ermolaev",
            city="Москва",
            is_active=False,
        )
        Training.objects.create(
            title="Тестовая тренировка",
            slug="test-training-2",
            description="Описание",
            city="Москва",
            coach=coach,
            is_active=True,
            type_prices={"group": 2000},
        )

        response = self.client.get(
            reverse("coach_detail", kwargs={"slug": coach.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Леонид Ермолаев")

    def test_inactive_coach_without_active_trainings_is_hidden(self) -> None:
        """Неактивный тренер без активных тренировок не должен быть видимым."""
        coach = Coach.objects.create(
            name="Скрытый тренер",
            slug="hidden-coach",
            city="Москва",
            is_active=False,
        )
        Training.objects.create(
            title="Архивная тренировка",
            slug="archived-training",
            description="Описание",
            city="Москва",
            coach=coach,
            is_active=False,
            type_prices={"group": 1500},
        )

        list_response = self.client.get(reverse("coach_list"), secure=True)
        detail_response = self.client.get(
            reverse("coach_detail", kwargs={"slug": coach.slug}),
            secure=True,
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, "Скрытый тренер")
        self.assertEqual(detail_response.status_code, 404)


class MyTrainingsAnonymousEnrollmentTests(TestCase):
    """Страница «Мои тренировки» не должна падать на заявках без игрока."""

    def setUp(self) -> None:
        self.coach_user = make_user(
            email="coach-my@test.local",
            first_name="Иван",
            last_name="Тренер",
        )
        self.coach = Coach.objects.create(
            user=self.coach_user,
            name="Иван Тренер",
            slug="ivan-trener-my",
            city="Москва",
            is_active=True,
        )
        self.training = Training.objects.create(
            title="Утренняя группа",
            slug="my-trainings-anon",
            description="Описание",
            city="Москва",
            coach=self.coach,
            is_active=True,
            type_prices={"group": 2000},
        )
        self.client.force_login(self.coach_user)

    def test_coach_page_renders_anonymous_enrollment_full_name(self) -> None:
        """Заявка без игрока отображает ФИО и не даёт 500."""
        TrainingEnrollment.objects.create(
            training=self.training,
            player=None,
            full_name="Анна Гость",
            telegram="@anna_guest",
        )

        response = self.client.get(reverse("my_trainings"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Анна Гость")
        self.assertContains(response, "my-trainings__btn")
        self.assertNotContains(response, "btn--filter-size")
        self.assertContains(response, "Telegram")

    def test_coach_page_renders_player_name_when_full_name_empty(self) -> None:
        """Старая заявка с игроком без ФИО показывает имя пользователя."""
        player = make_player(
            email_suffix="enrolled",
            first_name="Пётр",
            last_name="Игрок",
        )
        TrainingEnrollment.objects.create(
            training=self.training,
            player=player,
            full_name="",
        )

        response = self.client.get(reverse("my_trainings"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Пётр Игрок")

    def test_coach_page_renders_player_email_when_name_empty(self) -> None:
        """Если у игрока нет имени, на странице показывается email."""
        player = make_player(email_suffix="no-name")
        TrainingEnrollment.objects.create(
            training=self.training,
            player=player,
            full_name="",
        )

        response = self.client.get(reverse("my_trainings"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, player.user.email)

    def test_coach_page_renders_placeholder_when_anonymous_has_no_name(self) -> None:
        """Заявка без игрока и без ФИО не даёт 500 и показывает заглушку."""
        TrainingEnrollment.objects.create(
            training=self.training,
            player=None,
            full_name="",
        )

        response = self.client.get(reverse("my_trainings"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Анонимный игрок")

    def test_my_trainings_css_keeps_compact_equal_height_buttons(self) -> None:
        """Кнопки страницы имеют фиксированную высоту и не используют filter-size."""
        from pathlib import Path

        css_path = (
            Path(__file__).resolve().parents[2]
            / "static"
            / "css"
            / "pages"
            / "training.css"
        )
        css = css_path.read_text(encoding="utf-8")
        self.assertIn(".my-trainings__btn {", css)
        self.assertIn("height: 40px", css)
        self.assertIn("white-space: nowrap", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto", css)
