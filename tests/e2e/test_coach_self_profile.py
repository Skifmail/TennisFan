"""E2E: тренер сам редактирует, скрывает, публикует и удаляет свой профиль."""

from django.test import TestCase
from django.urls import reverse

from apps.core.sports import VenueSport
from apps.training.models import Coach, Training, TrainingEnrollment
from tests.support.factories import make_user


class CoachSelfProfileTests(TestCase):
    """Управление тренером собственным профилем с сайта."""

    def setUp(self) -> None:
        self.coach_user = make_user(email="self-coach@test.local")
        self.coach = Coach.objects.create(
            user=self.coach_user,
            name="Алексей Кочедыков",
            slug="aleksei-kochedykov",
            city="Раменское",
            is_active=True,
        )
        self.training = Training.objects.create(
            title="Утренняя группа",
            slug="self-coach-training",
            description="Описание",
            city="Раменское",
            coach=self.coach,
            is_active=True,
            type_prices={"group": 2000},
        )
        self.client.force_login(self.coach_user)

    def _profile_payload(self, **overrides: str) -> dict[str, object]:
        """Собрать валидные данные формы профиля с переопределениями."""
        data: dict[str, object] = {
            "name": "Алексей Кочедыков",
            "bio": "Новая биография",
            "experience_years": "12",
            "specialization": "Взрослые",
            "phone": "",
            "telegram": "@kochedykov",
            "whatsapp": "",
            "max_contact": "",
            "city": "Жуковский",
            "sports": ["tennis", "padel"],
        }
        data.update(overrides)
        return data

    def test_my_trainings_shows_profile_controls(self) -> None:
        """На странице «Мои тренировки» есть управление профилем."""
        response = self.client.get(reverse("my_trainings"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("coach_profile_edit"))
        self.assertContains(response, reverse("coach_profile_deactivate"))
        self.assertContains(response, reverse("coach_profile_delete"))

    def test_coach_edits_all_profile_fields(self) -> None:
        """Тренер сохраняет изменения профиля, URL карточки не меняется."""
        response = self.client.post(
            reverse("coach_profile_edit"),
            self._profile_payload(),
            secure=True,
        )

        self.assertRedirects(
            response, reverse("my_trainings"), fetch_redirect_response=False
        )
        self.coach.refresh_from_db()
        self.assertEqual(self.coach.bio, "Новая биография")
        self.assertEqual(self.coach.experience_years, 12)
        self.assertEqual(self.coach.city, "Жуковский")
        self.assertEqual(self.coach.telegram, "@kochedykov")
        self.assertEqual(self.coach.sport, VenueSport.BOTH)
        self.assertEqual(self.coach.slug, "aleksei-kochedykov")

    def test_blank_name_is_rejected(self) -> None:
        """Пустое имя не сохраняется."""
        response = self.client.post(
            reverse("coach_profile_edit"),
            self._profile_payload(name="   "),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.coach.refresh_from_db()
        self.assertEqual(self.coach.name, "Алексей Кочедыков")

    def test_deactivate_hides_profile_and_trainings(self) -> None:
        """Скрытие убирает тренера и его тренировки из публичного раздела."""
        response = self.client.post(reverse("coach_profile_deactivate"), secure=True)

        self.assertRedirects(
            response, reverse("my_trainings"), fetch_redirect_response=False
        )
        self.coach.refresh_from_db()
        self.training.refresh_from_db()
        self.assertFalse(self.coach.is_active)
        self.assertFalse(self.training.is_active)
        detail = self.client.get(
            reverse("coach_detail", kwargs={"slug": self.coach.slug}), secure=True
        )
        self.assertEqual(detail.status_code, 404)

    def test_activate_publishes_profile(self) -> None:
        """Публикация снова показывает профиль в разделе тренеров."""
        self.coach.is_active = False
        self.coach.save(update_fields=["is_active"])
        Training.objects.filter(pk=self.training.pk).update(is_active=False)

        self.client.post(reverse("coach_profile_activate"), secure=True)

        self.coach.refresh_from_db()
        self.assertTrue(self.coach.is_active)
        detail = self.client.get(
            reverse("coach_detail", kwargs={"slug": self.coach.slug}), secure=True
        )
        self.assertEqual(detail.status_code, 200)

    def test_status_toggles_require_post(self) -> None:
        """GET не меняет статус профиля."""
        response = self.client.get(reverse("coach_profile_deactivate"), secure=True)

        self.assertEqual(response.status_code, 405)
        self.coach.refresh_from_db()
        self.assertTrue(self.coach.is_active)

    def test_delete_confirmation_shows_counts(self) -> None:
        """Подтверждение показывает, что будет удалено, и ничего не удаляет."""
        TrainingEnrollment.objects.create(
            training=self.training, full_name="Анна Гость", telegram="@anna"
        )

        response = self.client.get(reverse("coach_profile_delete"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Будут удалены тренировки: 1")
        self.assertContains(response, "Будут удалены записи игроков на них: 1")
        self.assertTrue(Coach.objects.filter(pk=self.coach.pk).exists())

    def test_delete_removes_profile_and_trainings_keeps_user(self) -> None:
        """Удаление убирает профиль, тренировки и записи; аккаунт остаётся."""
        TrainingEnrollment.objects.create(
            training=self.training, full_name="Анна Гость", telegram="@anna"
        )

        response = self.client.post(reverse("coach_profile_delete"), secure=True)

        self.assertRedirects(
            response, reverse("my_trainings"), fetch_redirect_response=False
        )
        self.assertFalse(Coach.objects.filter(pk=self.coach.pk).exists())
        self.assertFalse(Training.objects.filter(pk=self.training.pk).exists())
        self.assertEqual(TrainingEnrollment.objects.count(), 0)
        self.coach_user.refresh_from_db()
        page = self.client.get(reverse("my_trainings"), secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, reverse("coach_profile_edit"))

    def test_non_coach_cannot_manage_profile(self) -> None:
        """Пользователь без профиля тренера не получает доступа."""
        other = make_user(email="not-coach@test.local")
        self.client.force_login(other)

        edit = self.client.get(reverse("coach_profile_edit"), secure=True)
        delete = self.client.post(reverse("coach_profile_delete"), secure=True)

        self.assertRedirects(
            edit, reverse("my_trainings"), fetch_redirect_response=False
        )
        self.assertRedirects(
            delete, reverse("my_trainings"), fetch_redirect_response=False
        )
        self.assertTrue(Coach.objects.filter(pk=self.coach.pk).exists())

    def test_anonymous_cannot_open_profile_edit(self) -> None:
        """Аноним перенаправляется на вход."""
        self.client.logout()

        response = self.client.get(reverse("coach_profile_edit"), secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response["Location"], reverse("my_trainings"))
