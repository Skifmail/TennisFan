"""Интеграционные тесты: уведомления о клубных турнирах."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase

from apps.clubs.models import (
    Club,
    ClubMember,
    ClubMemberRole,
    ClubMemberStatus,
    ClubNotificationConfig,
    ClubNotificationSettings,
)
from apps.core.models import UserTelegramLink
from apps.telegram_bot.notifications import (
    _format_new_tournament_message,
    _send_new_tournament_notification,
)
from apps.tournaments.models import Tournament, TournamentFormat, TournamentStatus
from apps.users.models import User


class NewClubTournamentNotificationTests(TestCase):
    """Проверки рассылки о новых клубных турнирах."""

    def setUp(self) -> None:
        self.club = Club.objects.create(
            name="Раменский клуб",
            slug="ram-club",
            city="Раменское",
            address="ул. Центральная, 1",
            email="club@test.local",
            admin_name="Администратор клуба",
        )
        ClubNotificationConfig.objects.create(
            club=self.club,
            notify_by_telegram=True,
            tournament_reminders_enabled=True,
        )

        self.member_user = User.objects.create_user(
            email="member@test.local",
            password="testpass123",
        )
        ClubMember.objects.create(
            club=self.club,
            user=self.member_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        ClubNotificationSettings.objects.create(
            club=self.club,
            user=self.member_user,
            telegram_enabled=True,
        )
        UserTelegramLink.objects.create(
            user=self.member_user,
            user_bot_chat_id=10101,
        )

        self.outsider = User.objects.create_user(
            email="outsider@test.local",
            password="testpass123",
        )
        UserTelegramLink.objects.create(
            user=self.outsider,
            user_bot_chat_id=20202,
        )

        self.tournament = Tournament.objects.create(
            name="Раменский турнир",
            slug="ram-open",
            city="Раменское",
            club=self.club,
            start_date=date(2026, 3, 24),
            format=TournamentFormat.WEEKEND_DAY,
            status=TournamentStatus.UPCOMING,
        )

    @patch("apps.telegram_bot.notifications.bot.is_configured", return_value=True)
    @patch("apps.telegram_bot.notifications.bot.send_to_user")
    def test_club_tournament_notification_goes_only_to_active_club_members(
        self,
        mocked_send_to_user,
        _mocked_is_configured,
    ) -> None:
        """Клубный турнир отправляется только участникам конкретного клуба."""
        mocked_send_to_user.return_value = True

        _send_new_tournament_notification(self.tournament.pk)

        self.assertEqual(mocked_send_to_user.call_count, 1)
        chat_id, text = mocked_send_to_user.call_args.args
        self.assertEqual(chat_id, 10101)
        self.assertIn("Раменский клуб", text)
        self.assertNotIn("20202", str(mocked_send_to_user.call_args_list))

    def test_club_tournament_message_contains_club_name(self) -> None:
        """В тексте клубного уведомления есть название клуба."""
        text = _format_new_tournament_message(self.tournament)

        self.assertIn("Новый клубный турнир", text)
        self.assertIn("Клуб: Раменский клуб", text)
