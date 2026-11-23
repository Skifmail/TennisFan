"""Подробности заявки доступны только тренеру и не зависят от мессенджера."""

from datetime import UTC, datetime

from django.test import TestCase
from django.urls import reverse

from apps.courts.models import Court
from apps.training.models import Coach, Training, TrainingEnrollment
from tests.support.factories import make_player, make_user


class TrainingEnrollmentDetailTests(TestCase):
    """Контакты, данные заявки и проверка владельца тренировки."""

    def setUp(self) -> None:
        self.coach_user = make_user(email="enrollment-detail-coach@test.local")
        self.coach = Coach.objects.create(
            user=self.coach_user,
            name="Иван Тренер",
            slug="enrollment-detail-coach",
            city="Раменское",
        )
        self.training = Training.objects.create(
            title="Индивидуальная тренировка",
            slug="enrollment-detail-training",
            description="Занятие со взрослым игроком",
            city="Раменское",
            coach=self.coach,
        )
        self.court = Court.objects.create(
            name="Теннисный клуб Радуга",
            slug="enrollment-detail-court",
            city="Раменское",
            address="ул. Тестовая, 10",
            surface="хард",
        )
        self.enrollment = TrainingEnrollment.objects.create(
            training=self.training,
            assigned_coach=self.coach,
            assigned_at=datetime(2026, 11, 23, 8, 15, tzinfo=UTC),
            full_name="Юлия Соколова",
            telegram="Sokolova731",
            whatsapp="8 (999) 123-45-67",
            email="enrolled-client@test.local",
            desired_court=self.court,
            preferred_datetime=datetime(2026, 11, 25, 14, 30, tzinfo=UTC),
            message="Хочу заниматься по средам.\nМожно после работы?",
        )
        TrainingEnrollment.objects.filter(pk=self.enrollment.pk).update(
            created_at=datetime(2026, 11, 23, 8, 15, tzinfo=UTC),
            updated_at=datetime(2026, 11, 23, 8, 20, tzinfo=UTC),
        )
        self.enrollment.refresh_from_db()
        self.detail_url = reverse(
            "enrollment_detail", kwargs={"pk": self.enrollment.pk}
        )
        self.client.force_login(self.coach_user)

    def test_owner_sees_all_request_data_and_plain_contacts(self) -> None:
        """Все данные видны в HTML без открытия Telegram или выполнения JavaScript."""
        response = self.client.get(self.detail_url, secure=True)

        self.assertTemplateUsed(response, "training/enrollment_detail.html")
        for value in (
            "Юлия Соколова",
            "Индивидуальная тренировка",
            "Раменское",
            "Теннисный клуб Радуга",
            "ул. Тестовая, 10",
            "Sokolova731",
            "8 (999) 123-45-67",
            "enrolled-client@test.local",
            "25.11.2026 17:30",
            "23.11.2026 11:15",
            "23.11.2026 11:20",
            "Ожидает",
            "Хочу заниматься по средам.",
            "Можно после работы?",
        ):
            with self.subTest(value=value):
                self.assertContains(response, value)

    def test_detail_offers_direct_links_and_a_phone_call(self) -> None:
        """Мессенджеры открываются напрямую, а номер можно набрать без WhatsApp."""
        response = self.client.get(self.detail_url, secure=True)

        self.assertContains(response, 'href="https://t.me/Sokolova731"')
        self.assertContains(response, 'href="https://wa.me/79991234567"')
        self.assertContains(response, 'href="tel:+79991234567"')
        self.assertContains(response, 'href="mailto:enrolled-client@test.local"')
        self.assertNotContains(response, "/contact/telegram/")

    def test_my_trainings_has_a_link_to_this_enrollment(self) -> None:
        """Тренер может открыть конкретную заявку из своего кабинета."""
        response = self.client.get(reverse("my_trainings"), secure=True)

        self.assertContains(response, f'href="{self.detail_url}"')
        self.assertContains(response, "Открыть заявку")

    def test_viewing_and_reloading_do_not_mark_client_as_contacted(self) -> None:
        """GET и HEAD сохраняют статус и время обновления заявки."""
        updated_at = self.enrollment.updated_at
        for method in (self.client.get, self.client.get, self.client.head):
            with self.subTest(method=method.__name__):
                response = method(self.detail_url, secure=True)
                self.assertEqual(response.status_code, 200)
                self.enrollment.refresh_from_db()
                self.assertEqual(
                    self.enrollment.status, TrainingEnrollment.Status.PENDING
                )
                self.assertEqual(self.enrollment.updated_at, updated_at)

    def test_post_is_not_allowed(self) -> None:
        """Страница подробностей не принимает изменения заявки через POST."""
        response = self.client.post(self.detail_url, secure=True)

        self.assertEqual(response.status_code, 405)

    def test_anonymous_is_redirected_to_login(self) -> None:
        """Контакты не раскрываются гостю, даже знающему адрес страницы."""
        self.client.logout()

        response = self.client.get(self.detail_url, secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertIn("next=", response["Location"])
        self.assertNotContains(response, "enrolled-client@test.local", status_code=302)

    def test_another_coach_cannot_read_the_enrollment(self) -> None:
        """Подмена номера заявки не даёт доступ другому тренеру."""
        other_user = make_user(email="other-detail-coach@test.local")
        Coach.objects.create(
            user=other_user,
            name="Другой тренер",
            slug="other-detail-coach",
            city="Москва",
        )
        self.client.force_login(other_user)

        response = self.client.get(self.detail_url, secure=True)

        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "enrolled-client@test.local", status_code=404)

    def test_client_and_staff_without_own_training_cannot_read_contacts(self) -> None:
        """Доступ выдаётся владельцу тренировки, а не по наличию аккаунта или роли."""
        player = make_player(email_suffix="detail-requester", first_name="Юлия")
        TrainingEnrollment.objects.filter(pk=self.enrollment.pk).update(player=player)
        staff = make_user(
            email="detail-staff@test.local", is_staff=True, is_superuser=True
        )
        for user in (player.user, staff):
            with self.subTest(user=user.pk):
                self.client.force_login(user)
                response = self.client.get(self.detail_url, secure=True)
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(response, "Sokolova731", status_code=404)

    def test_missing_enrollment_returns_404(self) -> None:
        """Несуществующая заявка не вызывает ошибку сервера."""
        self.enrollment.delete()

        response = self.client.get(self.detail_url, secure=True)

        self.assertEqual(response.status_code, 404)

    def test_archived_training_is_still_accessible_to_its_owner(self) -> None:
        """Снятие тренировки с публикации не закрывает её заявки от владельца."""
        Training.objects.filter(pk=self.training.pk).update(is_active=False)
        Coach.objects.filter(pk=self.coach.pk).update(is_active=False)

        response = self.client.get(self.detail_url, secure=True)

        self.assertContains(response, "Юлия Соколова")
        public_url = reverse("training_detail", kwargs={"slug": self.training.slug})
        self.assertNotContains(response, f'href="{public_url}"')

    def test_unassigned_training_does_not_expose_contacts(self) -> None:
        """Тренер не видит контакты, когда заявка больше ему не назначена."""
        TrainingEnrollment.objects.filter(pk=self.enrollment.pk).update(
            assigned_coach=None
        )

        response = self.client.get(self.detail_url, secure=True)

        self.assertEqual(response.status_code, 404)

    def test_empty_optional_fields_have_clear_placeholders(self) -> None:
        """Отсутствующие корт, время, пожелания и контакты не ломают страницу."""
        TrainingEnrollment.objects.filter(pk=self.enrollment.pk).update(
            desired_court=None,
            preferred_datetime=None,
            message="",
            telegram="",
            whatsapp="",
            email="",
        )

        response = self.client.get(self.detail_url, secure=True)

        self.assertContains(response, "Не указан")
        self.assertContains(response, "Не указано")
        self.assertContains(response, "Не указаны")
        self.assertContains(response, "Клиент не указал контакты для связи.")
        self.assertNotContains(response, 'href="tel:')
        self.assertNotContains(response, 'id="enrollment-email"')
        self.assertNotContains(response, 'data-copy-contact="')

    def test_invalid_phone_is_still_visible_as_text(self) -> None:
        """Некорректный старый номер можно прочитать, даже если ссылка не строится."""
        TrainingEnrollment.objects.filter(pk=self.enrollment.pk).update(
            telegram="", email="", whatsapp="номер не распознан"
        )

        response = self.client.get(self.detail_url, secure=True)

        self.assertContains(response, "номер не распознан")
        self.assertNotContains(response, 'href="tel:')
        self.assertNotContains(response, "https://wa.me/")

    def test_legacy_request_uses_player_name_without_extra_profile_contacts(
        self,
    ) -> None:
        """Имя старой заявки берётся из профиля, а несданные контакты не раскрываются."""
        player = make_player(
            email_suffix="detail-legacy", first_name="Пётр", last_name="Игрок"
        )
        TrainingEnrollment.objects.filter(pk=self.enrollment.pk).update(
            full_name="", player=player, telegram="", whatsapp="", email=""
        )

        response = self.client.get(self.detail_url, secure=True)

        self.assertContains(response, "Пётр Игрок")
        email = player.user.email
        assert email is not None
        self.assertNotContains(response, email)

    def test_user_input_is_escaped(self) -> None:
        """Имя, контакт и пожелания отображаются как текст, а не исполняемый HTML."""
        TrainingEnrollment.objects.filter(pk=self.enrollment.pk).update(
            full_name='<img src=x onerror="alert(1)">',
            telegram='<script>alert("contact")</script>',
            message='<script>alert("message")</script>\nВторая строка',
        )

        response = self.client.get(self.detail_url, secure=True)

        self.assertNotContains(response, '<img src=x onerror="alert(1)">')
        self.assertNotContains(response, '<script>alert("message")</script>')
        self.assertContains(response, "&lt;script&gt;")
        self.assertContains(response, "Вторая строка")

    def test_contacts_are_not_cached_or_indexed(self) -> None:
        """Персональные данные защищены от общего кеша и индексации поисковиками."""
        response = self.client.get(self.detail_url, secure=True)

        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertIn("noindex", response["X-Robots-Tag"])
