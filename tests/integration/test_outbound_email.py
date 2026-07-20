"""Тесты журнала исходящих писем."""

from django.core import mail
from django.test import TestCase, override_settings

from apps.core.email_service import send_donation_thanks_email
from apps.core.models import OutboundEmail
from apps.users.models import User


@override_settings(
    EMAIL_BACKEND="apps.core.mail.LoggingEmailBackend",
    EMAIL_BACKEND_INNER="django.core.mail.backends.locmem.EmailBackend",
)
class OutboundEmailLogTestCase(TestCase):
    """Отправка писем сохраняет копию в ``OutboundEmail``."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="outbound@test.local",
            password="x",
            first_name="Иван",
            last_name="Тестов",
        )

    def test_successful_send_is_logged_with_html(self) -> None:
        """Успешная отправка попадает в журнал с HTML-телом."""
        ok = send_donation_thanks_email(self.user, "500")
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        row = OutboundEmail.objects.get()
        self.assertEqual(row.to_email, "outbound@test.local")
        self.assertEqual(row.user_id, self.user.pk)
        self.assertEqual(row.status, OutboundEmail.Status.SENT)
        self.assertIn("Спасибо", row.subject)
        self.assertTrue(row.body_html)
        self.assertTrue(row.body_text)

    def test_admin_preview_renders_iframe(self) -> None:
        """В карточке админки HTML показывается через iframe."""
        from django.contrib import admin as django_admin

        from apps.core.admin import OutboundEmailAdmin

        send_donation_thanks_email(self.user, "100")
        row = OutboundEmail.objects.get()
        preview = OutboundEmailAdmin(OutboundEmail, django_admin.site).body_preview(row)
        self.assertIn("iframe", str(preview))
        self.assertIn("srcdoc", str(preview))
