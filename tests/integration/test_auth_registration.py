"""Интеграционные тесты регистрации и входа на объединённой странице."""

from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.core.models import LegalAcceptanceLog, UserConsent
from apps.users.models import Player, User


def registration_payload(**overrides: object) -> dict[str, object]:
    """Собрать данные формы регистрации.

    Args:
        **overrides: Переопределения отдельных полей.

    Returns:
        dict[str, object]: Данные POST-запроса.
    """
    data: dict[str, object] = {
        "form_type": "register",
        "first_name": "Иван",
        "last_name": "Тестов",
        "email": "new-player@test.local",
        "phone": "+7 999 123-45-67",
        "city": "Москва",
        "ntrp_level": "3.75",
        "password": "StrongPass123",
        "password_confirm": "StrongPass123",
        "agree_legal": "on",
        "agree_terms": "on",
    }
    data.update(overrides)
    return data


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_ADMIN_CHAT_ID="",
    TELEGRAM_ADMIN_CHAT_IDS=[],
)
class RegistrationFlowTestCase(TestCase):
    """Регистрация создаёт аккаунт, профиль и сессию без дублей."""

    def setUp(self) -> None:
        cache.clear()
        self.client = Client()

    def test_registration_creates_user_player_and_logs_in(self) -> None:
        response = self.client.post(
            reverse("auth"), data=registration_payload(), secure=True
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))
        user = User.objects.get(email="new-player@test.local")
        self.assertEqual(user.first_name, "Иван")
        self.assertFalse(user.email_verified)
        player = Player.objects.get(user=user)
        self.assertEqual(player.ntrp_level, Decimal("3.75"))
        self.assertEqual(player.city, "Москва")
        self.assertEqual(
            str(self.client.session.get("_auth_user_id")),
            str(user.pk),
        )
        self.assertTrue(
            any("подтвердите ваш email" in message.subject for message in mail.outbox)
        )

    def test_short_password_returns_to_step1_with_error_summary(self) -> None:
        response = self.client.post(
            reverse("auth"),
            data=registration_payload(
                password="Abcdef123", password_confirm="Abcdef123"
            ),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["show_register_step2"])
        self.assertContains(response, "Проверьте поля формы")
        self.assertContains(response, "10")
        self.assertFalse(User.objects.filter(email="new-player@test.local").exists())

    def test_registration_requires_independent_personal_data_and_terms(self) -> None:
        for field in ("agree_legal", "agree_terms"):
            with self.subTest(field=field):
                response = self.client.post(
                    reverse("auth"), registration_payload(**{field: ""}), secure=True
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["register_form"].errors)
                self.assertFalse(response.context["show_register_step2"])
                self.assertFalse(
                    User.objects.filter(email="new-player@test.local").exists()
                )
                self.assertFalse(LegalAcceptanceLog.objects.exists())

    def test_registration_logs_separate_documents_without_paid_offer(self) -> None:
        self.client.post(reverse("auth"), registration_payload(), secure=True)
        user = User.objects.get(email="new-player@test.local")
        entries = LegalAcceptanceLog.objects.filter(user=user)
        self.assertEqual(
            set(entries.values_list("document_slug", flat=True)),
            {"personal-data", "terms", "privacy"},
        )
        self.assertTrue(
            entries.get(document_slug="personal-data").metadata["document_text"]
        )
        self.assertEqual(
            entries.get(document_slug="privacy").metadata["event"], "notice_presented"
        )
        self.assertFalse(
            UserConsent.objects.filter(
                user=user, consent_type="platform_offer"
            ).exists()
        )

    def test_comma_in_ntrp_is_accepted(self) -> None:
        response = self.client.post(
            reverse("auth"),
            data=registration_payload(ntrp_level="3,75"),
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        player = Player.objects.get(user__email="new-player@test.local")
        self.assertEqual(player.ntrp_level, Decimal("3.75"))

    def test_repeated_submission_does_not_create_duplicate(self) -> None:
        self.client.post(reverse("auth"), data=registration_payload(), secure=True)
        response = self.client.post(
            reverse("auth"), data=registration_payload(), secure=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "уже зарегистрирован")
        self.assertEqual(User.objects.filter(email="new-player@test.local").count(), 1)
        self.assertEqual(
            Player.objects.filter(user__email="new-player@test.local").count(), 1
        )

    def test_rejected_registration_is_logged(self) -> None:
        with self.assertLogs("apps.users.views", level="WARNING") as captured:
            self.client.post(
                reverse("auth"),
                data=registration_payload(password="short", password_confirm="short"),
                secure=True,
            )

        self.assertTrue(
            any("registration rejected" in line for line in captured.output)
        )

    def test_registration_throttled_after_limit(self) -> None:
        with patch("apps.users.views.REGISTRATION_LIMIT", 2):
            for _ in range(3):
                response = self.client.post(
                    reverse("auth"),
                    data=registration_payload(
                        password="short", password_confirm="short"
                    ),
                    secure=True,
                )

        self.assertContains(response, "Слишком много попыток регистрации")

    def test_login_password_field_has_unique_id(self) -> None:
        response = self.client.get(reverse("auth"), {"mode": "login"}, secure=True)
        html = response.content.decode()

        self.assertEqual(html.count('id="id_password"'), 1)
        self.assertIn('id="id_login_password"', html)

    def test_password_fields_have_live_hints(self) -> None:
        response = self.client.get(reverse("auth"), secure=True)
        html = response.content.decode()

        self.assertIn('id="password-error" aria-live="polite"', html)
        self.assertIn('id="password_confirm-error" aria-live="polite"', html)

    def test_auth_logger_writes_to_dedicated_file(self) -> None:
        files = [
            str(getattr(handler, "baseFilename", ""))
            for handler in logging.getLogger("apps.users.views").handlers
        ]

        self.assertTrue(
            any(path.endswith("django_auth.log") for path in files),
            files,
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_ADMIN_CHAT_ID="",
    TELEGRAM_ADMIN_CHAT_IDS=[],
)
class LoginFormFeedbackTestCase(TestCase):
    """Форма входа показывает реальную причину и сохраняет email."""

    def setUp(self) -> None:
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            email="login-check@test.local",
            password="StrongPass123",
            first_name="Иван",
            last_name="Тестов",
        )

    def test_wrong_password_shows_real_error_and_keeps_email(self) -> None:
        response = self.client.post(
            reverse("auth"),
            data={
                "form_type": "login",
                "username": "login-check@test.local",
                "password": "wrong-password",
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('value="login-check@test.local"', html)
        self.assertIn("введите правильные", html)

    def test_successful_login_is_logged(self) -> None:
        with self.assertLogs("apps.users.views", level="INFO") as captured:
            response = self.client.post(
                reverse("auth"),
                data={
                    "form_type": "login",
                    "username": "login-check@test.local",
                    "password": "StrongPass123",
                },
                secure=True,
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("login success" in line for line in captured.output))
