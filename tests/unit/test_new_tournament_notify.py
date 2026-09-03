"""Юнит-тесты уведомлений о новом турнире и разделов писем."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.contrib import admin as django_admin
from django.core import mail
from django.test import TestCase, override_settings

from apps.core.email_categories import classify_outbound_email
from apps.core.email_service import send_new_tournament_email
from apps.core.models import (
    NewTournamentOutboundEmail,
    OutboundEmail,
    RegistrationOutboundEmail,
)
from apps.telegram_bot.notifications import (
    _notify_new_tournament_lk_and_email,
    _send_new_tournament_notification,
)
from apps.tournaments.models import Tournament
from apps.users.models import Notification, Player, User


class EmailCategoryClassifyTestCase(TestCase):
    """Эвристика разделов писем."""

    def test_explicit_category_wins(self) -> None:
        """Явная категория важнее темы."""
        self.assertEqual(
            classify_outbound_email(
                category="new_tournament",
                subject="Добро пожаловать в TennisFan!",
            ),
            OutboundEmail.Category.NEW_TOURNAMENT,
        )

    def test_subject_heuristics(self) -> None:
        """Тема письма определяет раздел."""
        self.assertEqual(
            classify_outbound_email(subject="Добро пожаловать в TennisFan!"),
            OutboundEmail.Category.REGISTRATION,
        )
        self.assertEqual(
            classify_outbound_email(subject="TennisFan: новый турнир «Кубок»"),
            OutboundEmail.Category.NEW_TOURNAMENT,
        )
        self.assertEqual(
            classify_outbound_email(subject="TennisFan: пароль аккаунта изменён"),
            OutboundEmail.Category.SECURITY,
        )


@override_settings(
    EMAIL_BACKEND="apps.core.mail.LoggingEmailBackend",
    EMAIL_BACKEND_INNER="django.core.mail.backends.locmem.EmailBackend",
)
class NewTournamentNotifyTestCase(TestCase):
    """ЛК + email при создании турнира."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="player@test.local",
            password="x",
            first_name="Анна",
            last_name="Игрок",
        )
        Player.objects.create(user=self.user, city="Москва", is_bye=False)
        self.tournament = Tournament.objects.create(
            name="Кубок лета",
            slug="kubok-leta-notify",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )

    def test_send_new_tournament_email_branded_and_logged(self) -> None:
        """Письмо о турнире сохраняется в журнале с разделом new_tournament."""
        ok = send_new_tournament_email(self.user, self.tournament)
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("новый турнир", mail.outbox[0].subject.lower())
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("TENNISFAN", html)
        self.assertIn(self.tournament.name, html)
        # Квадратный logo.png не должен сплющиваться атрибутами width≠height.
        self.assertIn('width="80"', html)
        self.assertIn('height="80"', html)
        self.assertNotIn('width="120"', html)
        self.assertNotIn('height="56"', html)
        row = OutboundEmail.objects.get()
        self.assertEqual(row.category, OutboundEmail.Category.NEW_TOURNAMENT)
        self.assertEqual(row.user_id, self.user.pk)

    def test_lk_and_email_blast(self) -> None:
        """Рассылка создаёт уведомление в ЛК и письмо."""
        lk_count, email_count = _notify_new_tournament_lk_and_email(self.tournament)
        self.assertEqual(lk_count, 1)
        self.assertEqual(email_count, 1)
        note = Notification.objects.get(user=self.user)
        self.assertIn("Кубок лета", note.message)
        self.assertIn(self.tournament.slug, note.url)
        self.assertEqual(OutboundEmail.objects.count(), 1)

    @patch("apps.telegram_bot.notifications.bot.is_configured", return_value=False)
    def test_send_without_telegram_still_notifies_lk_email(
        self, _mock_bot: object
    ) -> None:
        """Без Telegram-бота ЛК и email всё равно уходят."""
        _send_new_tournament_notification(self.tournament.pk)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)
        self.assertEqual(OutboundEmail.objects.count(), 1)

    def test_admin_proxy_filters_new_tournament(self) -> None:
        """Раздел «Новые турниры» показывает только свою категорию."""
        send_new_tournament_email(self.user, self.tournament)
        from apps.core.admin import NewTournamentOutboundEmailAdmin
        from apps.core.email_service import send_donation_thanks_email

        send_donation_thanks_email(self.user, "100")
        self.assertEqual(OutboundEmail.objects.count(), 2)
        admin_obj = NewTournamentOutboundEmailAdmin(
            NewTournamentOutboundEmail, django_admin.site
        )
        qs = admin_obj.get_queryset(request=None)  # type: ignore[arg-type]
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.get().category, OutboundEmail.Category.NEW_TOURNAMENT)

        from apps.core.admin import RegistrationOutboundEmailAdmin

        # donation is subscription, not registration
        reg_admin = RegistrationOutboundEmailAdmin(
            RegistrationOutboundEmail, django_admin.site
        )
        self.assertEqual(reg_admin.get_queryset(request=None).count(), 0)  # type: ignore[arg-type]
