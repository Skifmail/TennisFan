"""Интеграция: недобор участников, уведомления клубу, автоотмена через 3ч."""

from datetime import date, timedelta
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    Club,
    ClubMember,
    ClubMemberRole,
    ClubMemberStatus,
    ClubNotificationConfig,
    ClubNotificationSettings,
)
from apps.core.telegram_notify import notify_tournament_insufficient_participants
from apps.tournaments.fan import check_and_generate_past_deadline_brackets
from apps.tournaments.models import Tournament, TournamentFormat, TournamentStatus
from apps.users.models import User


@override_settings(
    SITE_URL="https://tennisfan.ru",
    LANGUAGE_CODE="ru",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class TournamentInsufficientLifecycleTestCase(TestCase):
    """Недобор → уведомление клубу с клубным URL → автоотмена через 3ч."""

    def setUp(self) -> None:
        self.admin = User.objects.create_user(
            email="club-owner@test.local", password="x"
        )
        self.club = Club.objects.create(
            name="Клуб недобора",
            slug="underfill-club",
            city="Москва",
            address="ул. 1",
            email="club@test.local",
            admin_name="Админ",
        )
        ClubMember.objects.create(
            club=self.club,
            user=self.admin,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )
        ClubNotificationConfig.objects.get_or_create(
            club=self.club,
            defaults={"notify_by_email": True, "notify_by_telegram": False},
        )
        ClubNotificationSettings.objects.get_or_create(
            user=self.admin,
            club=self.club,
            defaults={
                "is_enabled": True,
                "email_enabled": True,
                "telegram_enabled": False,
            },
        )
        past = timezone.now() - timedelta(hours=1)
        self.tournament = Tournament.objects.create(
            name="Недобор тест",
            slug="underfill-tour",
            city="Москва",
            club=self.club,
            start_date=date.today() + timedelta(days=7),
            format=TournamentFormat.SINGLE_ELIMINATION,
            status=TournamentStatus.UPCOMING,
            entry_fee=500,
            min_participants=4,
            registration_deadline=past,
            bracket_generated=False,
        )

    @patch("django.core.cache.cache.get", return_value=None)
    @patch("apps.core.telegram_notify.send_admin_message", return_value=True)
    def test_insufficient_notifies_club_with_edit_url(
        self, _admin_msg, _cache_get
    ) -> None:
        check_and_generate_past_deadline_brackets()
        self.tournament.refresh_from_db()
        self.assertIsNotNone(self.tournament.insufficient_participants_notified_at)
        self.assertEqual(self.tournament.status, TournamentStatus.UPCOMING)
        self.assertTrue(mail.outbox)
        msg = mail.outbox[-1]
        html = msg.alternatives[0][0] if msg.alternatives else msg.body
        edit_path = reverse(
            "clubs:tournament_edit",
            kwargs={"slug": self.club.slug, "tournament_id": self.tournament.pk},
        )
        self.assertIn(edit_path, html)
        self.assertIn("https://tennisfan.ru", html)
        self.assertNotIn("/admin/tournaments/tournament/", html)

    @patch("django.core.cache.cache.get", return_value=None)
    @patch("apps.core.telegram_notify.send_admin_message", return_value=True)
    def test_auto_cancel_after_three_hours(self, _admin_msg, _cache_get) -> None:
        self.tournament.insufficient_participants_notified_at = (
            timezone.now() - timedelta(hours=3, minutes=5)
        )
        self.tournament.save(
            update_fields=["insufficient_participants_notified_at", "updated_at"]
        )
        check_and_generate_past_deadline_brackets()
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.CANCELLED)

    @patch("apps.core.telegram_notify.send_admin_message", return_value=True)
    def test_platform_notify_uses_club_edit_absolute_url(self, mock_send) -> None:
        notify_tournament_insufficient_participants(self.tournament)
        self.assertTrue(mock_send.called)
        msg = mock_send.call_args[0][0]
        edit_path = reverse(
            "clubs:tournament_edit",
            kwargs={"slug": self.club.slug, "tournament_id": self.tournament.pk},
        )
        self.assertIn(f"https://tennisfan.ru{edit_path}", msg)
        self.assertNotIn("admin/tournaments/tournament/", msg)
