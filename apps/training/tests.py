"""
Тесты публичного раздела тренировок и тренеров.
"""

from django.test import TestCase
from django.urls import reverse

from apps.training.models import Coach, Training
from apps.users.models import User


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
