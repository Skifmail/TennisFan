from datetime import timedelta

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.email_service import send_email_verification
from apps.users.models import EmailVerificationToken, Player, User


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailVerificationFlowTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="verify@test.local",
            password="testpass123",
            first_name="Вериф",
            last_name="Юзер",
            email_verified=False,
        )
        self.player = Player.objects.create(user=self.user, city="Москва")

    def test_confirm_email_by_token_marks_user_verified(self) -> None:
        send_email_verification(self.user)
        token = EmailVerificationToken.objects.get(user=self.user, used_at__isnull=True)
        response = self.client.get(
            reverse("verify_email_confirm", kwargs={"token": token.token}),
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        token.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertIsNotNone(token.used_at)

    def test_confirm_email_with_expired_token_rejected(self) -> None:
        send_email_verification(self.user)
        token = EmailVerificationToken.objects.get(user=self.user, used_at__isnull=True)
        token.expires_at = timezone.now() - timedelta(minutes=1)
        token.save(update_fields=["expires_at"])
        response = self.client.get(
            reverse("verify_email_confirm", kwargs={"token": token.token}),
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)

    def test_resend_endpoint_sends_new_email(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(reverse("verify_email_resend"), secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
