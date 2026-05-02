"""Тесты Telegram-бота."""

from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

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
from apps.telegram_bot.private_chat import get_private_chat_access_status
from apps.telegram_bot.telegram_http import is_telegram_api_enabled
from apps.tournaments.models import Tournament, TournamentFormat, TournamentStatus
from apps.users.models import User


class TelegramApiEnabledParsingTests(SimpleTestCase):
    """Проверка разбора TELEGRAM_ENABLED (окружение имеет приоритет над settings)."""

    def test_env_false_disables_even_if_settings_true(self) -> None:
        """Строка false в окружении отключает API независимо от settings."""
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "false"}, clear=False):
            with override_settings(TELEGRAM_ENABLED=True):
                self.assertFalse(is_telegram_api_enabled())

    def test_env_true_enables(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "true"}, clear=False):
            with override_settings(TELEGRAM_ENABLED=False):
                self.assertTrue(is_telegram_api_enabled())

    def test_env_empty_string_disables(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": ""}, clear=False):
            self.assertFalse(is_telegram_api_enabled())

    def test_fallback_settings_respects_bool_false(self) -> None:
        """Если ключа нет в os.environ, используется settings (bool)."""
        backup = os.environ.pop("TELEGRAM_ENABLED", None)
        try:
            with override_settings(TELEGRAM_ENABLED=False):
                self.assertFalse(is_telegram_api_enabled())
        finally:
            if backup is not None:
                os.environ["TELEGRAM_ENABLED"] = backup


class PrivateChatAccessStatusTests(SimpleTestCase):
    """Проверки бизнес-правил доступа в закрытый чат по подписке."""

    def test_access_denied_without_subscription(self) -> None:
        """Без подписки доступ в чат запрещен."""
        user = SimpleNamespace(subscription=None)

        allowed, reason = get_private_chat_access_status(user)

        self.assertFalse(allowed)
        self.assertEqual(reason, "Нет активной подписки.")

    def test_access_denied_for_expired_subscription(self) -> None:
        """Истекшая подписка не дает доступ в чат."""
        tier = SimpleNamespace(has_private_chat=True)
        subscription = SimpleNamespace(tier=tier, is_valid=lambda: False)
        user = SimpleNamespace(subscription=subscription)

        allowed, reason = get_private_chat_access_status(user)

        self.assertFalse(allowed)
        self.assertEqual(reason, "Подписка неактивна или истекла.")

    def test_access_denied_when_tier_has_no_private_chat(self) -> None:
        """Тариф без флага has_private_chat должен блокировать доступ."""
        tier = SimpleNamespace(has_private_chat=False)
        subscription = SimpleNamespace(tier=tier, is_valid=lambda: True)
        user = SimpleNamespace(subscription=subscription)

        allowed, reason = get_private_chat_access_status(user)

        self.assertFalse(allowed)
        self.assertEqual(reason, "Ваш тариф не включает доступ в закрытый чат.")

    def test_access_allowed_for_active_supported_tier(self) -> None:
        """Активная подписка с has_private_chat=True дает доступ."""
        tier = SimpleNamespace(has_private_chat=True)
        subscription = SimpleNamespace(tier=tier, is_valid=lambda: True)
        user = SimpleNamespace(subscription=subscription)

        allowed, reason = get_private_chat_access_status(user)

        self.assertTrue(allowed)
        self.assertEqual(reason, "")


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
        """Клубный турнир должен отправляться только участникам конкретного клуба."""
        mocked_send_to_user.return_value = True

        _send_new_tournament_notification(self.tournament.pk)

        self.assertEqual(mocked_send_to_user.call_count, 1)
        chat_id, text = mocked_send_to_user.call_args.args
        self.assertEqual(chat_id, 10101)
        self.assertIn("Раменский клуб", text)
        self.assertNotIn("20202", str(mocked_send_to_user.call_args_list))

    def test_club_tournament_message_contains_club_name(self) -> None:
        """В тексте клубного уведомления должно быть название клуба."""
        text = _format_new_tournament_message(self.tournament)

        self.assertIn("Новый клубный турнир", text)
        self.assertIn("Клуб: Раменский клуб", text)
