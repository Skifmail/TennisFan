"""Запись на тренировку: уведомления администраторам до распределения."""

from typing import Any

from django.core import mail
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.email_service import send_training_enrollment_email
from apps.core.models import OutboundEmail
from apps.courts.models import Court
from apps.training.models import Coach, Training, TrainingEnrollment
from apps.users.models import Notification
from tests.support.factories import make_user


def _make_court(*, name: str = "Корт Юг", slug: str = "enroll-mail-court") -> Court:
    """Создать активный корт в Москве для формы записи."""
    return Court.objects.create(
        name=name,
        slug=slug,
        city="Москва",
        address="ул. Тестовая, 1",
        surface="хард",
        is_active=True,
    )


def _make_training(
    *, coach: Coach | None, slug: str = "enroll-mail-training"
) -> Training:
    """Создать активную тренировку."""
    return Training.objects.create(
        title="Утренняя группа",
        slug=slug,
        description="Описание",
        city="Москва",
        coach=coach,
        is_active=True,
        type_prices={"group": 2000},
    )


def _email_html(message: mail.EmailMessage) -> str:
    """Возвращает HTML отправленного письма, проверяя тип MIME-содержимого."""
    assert isinstance(message, mail.EmailMultiAlternatives)
    html = message.alternatives[0][0]
    assert isinstance(html, str)
    return html


@override_settings(
    EMAIL_BACKEND="apps.core.mail.LoggingEmailBackend",
    EMAIL_BACKEND_INNER="django.core.mail.backends.locmem.EmailBackend",
)
class TrainingEnrollmentAdminEmailTestCase(TestCase):
    """При записи на тренировку администратор получает все данные заявки."""

    def setUp(self) -> None:
        self.coach_user = make_user(
            email="coach-mail@test.local",
            first_name="Иван",
            last_name="Тренер",
        )
        self.coach = Coach.objects.create(
            user=self.coach_user,
            name="Иван Тренер",
            slug="ivan-trener-mail",
            city="Москва",
            is_active=True,
        )
        self.training = _make_training(coach=self.coach)
        self.court = _make_court()
        self.admin_user = make_user(
            email="enrollment-admin@test.local", is_staff=True, is_superuser=True
        )

    def _post(self, *args: Any, **kwargs: Any) -> HttpResponse:
        """Отправить форму и выполнить уведомления после фиксации транзакции."""
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(*args, **kwargs)

    def test_enroll_sends_email_to_admin_with_request_data(self) -> None:
        """Письмо уходит только администратору и содержит контакты игрока."""
        url = reverse("training_enroll", kwargs={"slug": self.training.slug})
        response = self._post(
            url,
            {
                "full_name": "Анна Игрок",
                "contact_method": "telegram",
                "contact_value": "@anna_player",
                "desired_court": str(self.court.pk),
                "message": "Хочу в группу по вторникам",
                "agree_legal": "on",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(TrainingEnrollment.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["enrollment-admin@test.local"])
        self.assertIn("Утренняя группа", message.subject)
        html = _email_html(message)
        self.assertIn("Анна Игрок", html)
        self.assertIn("@anna_player", html)
        self.assertIn("Корт Юг", html)
        self.assertIn("Хочу в группу по вторникам", html)
        enrollment = TrainingEnrollment.objects.get()
        detail_url = reverse(
            "admin:training_trainingenrollment_change", args=[enrollment.pk]
        )
        self.assertIn(detail_url, html)
        self.assertIn("Открыть заявку", html)
        outbound = OutboundEmail.objects.get()
        self.assertEqual(outbound.category, OutboundEmail.Category.OTHER)
        self.assertEqual(outbound.to_email, "enrollment-admin@test.local")
        self.assertIn("Иван Тренер", html)
        self.assertIsNone(enrollment.assigned_coach_id)
        self.assertFalse(Notification.objects.filter(user=self.coach_user).exists())

    def test_enroll_sends_email_when_contact_is_whatsapp(self) -> None:
        """WhatsApp из заявки попадает в письмо администратору."""
        url = reverse("training_enroll", kwargs={"slug": self.training.slug})
        response = self._post(
            url,
            {
                "full_name": "Пётр Игрок",
                "contact_method": "whatsapp",
                "contact_value": "+79990001122",
                "agree_legal": "on",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        html = _email_html(mail.outbox[0])
        self.assertIn("Пётр Игрок", html)
        self.assertIn("+79990001122", html)

    @override_settings(NOTIFICATION_CENTER_ENABLED=True)
    def test_enroll_notification_links_to_enrollment_details(self) -> None:
        """Уведомление в ЛК ведёт сразу к подробностям новой заявки."""
        url = reverse("training_enroll", kwargs={"slug": self.training.slug})
        response = self._post(
            url,
            {
                "full_name": "Анна Игрок",
                "contact_method": "telegram",
                "contact_value": "@anna_player",
                "agree_legal": "on",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        note = Notification.objects.get(
            user=self.admin_user,
            event_key="training.enrollment",
        )
        enrollment = TrainingEnrollment.objects.get()
        self.assertEqual(
            note.url,
            reverse("admin:training_trainingenrollment_change", args=[enrollment.pk]),
        )

    def test_enroll_sends_email_when_contact_is_email(self) -> None:
        """Email из заявки попадает в письмо администратору."""
        url = reverse("training_enroll", kwargs={"slug": self.training.slug})
        response = self._post(
            url,
            {
                "full_name": "Мария Игрок",
                "contact_method": "email",
                "contact_value": "maria@test.local",
                "agree_legal": "on",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        html = _email_html(mail.outbox[0])
        self.assertIn("Мария Игрок", html)
        self.assertIn("maria@test.local", html)

    def test_enroll_succeeds_when_coach_has_no_email(self) -> None:
        """Отсутствие email у тренера не ломает запись."""
        coach = Coach.objects.create(
            name="Без почты",
            slug="no-email-coach",
            city="Москва",
            is_active=True,
        )
        training = _make_training(coach=coach, slug="enroll-no-email-training")
        url = reverse("training_enroll", kwargs={"slug": training.slug})
        response = self._post(
            url,
            {
                "full_name": "Гость",
                "contact_method": "email",
                "contact_value": "guest@test.local",
                "agree_legal": "on",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            TrainingEnrollment.objects.filter(
                training=training, full_name="Гость"
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.admin_user.email])

    def test_send_training_enrollment_email_notifies_admin_without_coach_user(
        self,
    ) -> None:
        """Даже без аккаунта исходного тренера уведомление получает администратор."""
        coach = Coach.objects.create(
            name="Без аккаунта",
            slug="no-user-coach",
            city="Москва",
            is_active=True,
        )
        training = _make_training(coach=coach, slug="enroll-no-user-training")
        enrollment = TrainingEnrollment.objects.create(
            training=training,
            full_name="Игрок",
            telegram="@player",
        )

        ok = send_training_enrollment_email(enrollment)

        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.admin_user.email])
