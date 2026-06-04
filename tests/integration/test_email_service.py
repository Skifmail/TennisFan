"""Интеграционные тесты: сервис исходящей почты."""

from datetime import date

from django.core import mail
from django.test import TestCase, override_settings

from apps.core.email_service import (
    send_donation_thanks_email,
    send_email_verification,
    send_phone_changed_email,
    send_tournament_entry_fancoin_confirmed_email,
    send_tournament_entry_receipt_email,
)
from apps.tournaments.models import Tournament
from apps.users.models import EmailVerificationToken, Player, User


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailServiceTestCase(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="email-service@test.local",
            password="testpass123",
            first_name="Тест",
            last_name="Пользователь",
            email_verified=False,
        )
        self.player = Player.objects.create(user=self.user, city="Москва")

    def test_send_donation_thanks_email(self) -> None:
        ok = send_donation_thanks_email(self.user, "1000")
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Спасибо за поддержку", mail.outbox[0].subject)

    def test_send_tournament_entry_fancoin_confirmed_email(self) -> None:
        tournament = Tournament.objects.create(
            name="Тестовый турнир FT",
            slug="test-mail-tournament-ft",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        ok = send_tournament_entry_fancoin_confirmed_email(
            self.user,
            tournament,
            fancoin_spent=3,
            fancoin_balance=12,
            had_payment_request=True,
        )
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("подтверждено", mail.outbox[0].subject.lower())
        self.assertIn("не требуется", mail.outbox[0].alternatives[0][0])

    def test_send_tournament_entry_receipt_email(self) -> None:
        tournament = Tournament.objects.create(
            name="Тестовый турнир",
            slug="test-mail-tournament",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        ok = send_tournament_entry_receipt_email(
            self.user,
            tournament,
            amount="750",
            is_postpayment=False,
        )
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("оплата турнирного взноса", mail.outbox[0].subject.lower())

    def test_send_phone_changed_email(self) -> None:
        ok = send_phone_changed_email(self.user, "+79990000000", "+79991111111")
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("номер телефона", mail.outbox[0].subject)

    def test_send_email_verification_creates_token(self) -> None:
        ok = send_email_verification(self.user)
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        token = EmailVerificationToken.objects.get(user=self.user, used_at__isnull=True)
        self.assertIn(token.token, mail.outbox[0].alternatives[0][0])
