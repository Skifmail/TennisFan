"""Smoke-тесты Telegram: без реальных HTTP-вызовов к api.telegram.org."""

from __future__ import annotations

import os
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from apps.core.models import UserTelegramLink
from apps.telegram_bot import services as bot_services
from apps.telegram_bot.notifications import send_to_user_by_user
from apps.telegram_bot.telegram_http import (
    is_telegram_api_enabled,
    telegram_requests_proxies,
)
from tests.support.factories import make_user


class TelegramApiGuardSmokeTestCase(TestCase):
    """Исходящие вызовы блокируются при отключённом API."""

    def test_send_message_skips_http_when_disabled(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "false"}, clear=False):
            with patch("apps.telegram_bot.services.requests.post") as mocked_post:
                message_id, ok = bot_services.send_message(12345, "Тест")

        self.assertFalse(ok)
        self.assertIsNone(message_id)
        mocked_post.assert_not_called()

    def test_is_configured_false_without_token(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "true"}, clear=False):
            with override_settings(TELEGRAM_USER_BOT_TOKEN=""):
                self.assertFalse(bot_services.is_configured())

    def test_is_configured_true_with_token_and_enabled(self) -> None:
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "true"}, clear=False):
            with override_settings(TELEGRAM_USER_BOT_TOKEN="123:ABC"):
                self.assertTrue(bot_services.is_configured())
                self.assertTrue(is_telegram_api_enabled())


class TelegramNotificationFallbackTestCase(TestCase):
    """Уведомление пользователю без chat_id — email-дубль."""

    def test_send_to_user_by_user_falls_back_to_email(self) -> None:
        user = make_user(email="tg-fallback@test.local")
        with override_settings(USER_NOTIFICATIONS_EMAIL_ENABLED=True):
            ok = send_to_user_by_user(user, "<b>Тест</b> уведомления")

        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["tg-fallback@test.local"])
        self.assertIn("уведомления", mail.outbox[0].body)

    def test_send_to_user_by_user_uses_telegram_when_linked(self) -> None:
        user = make_user(email="tg-linked@test.local")
        UserTelegramLink.objects.create(user=user, user_bot_chat_id=999888)
        with patch(
            "apps.telegram_bot.notifications.bot.send_to_user",
            return_value=True,
        ) as mocked_send:
            ok = send_to_user_by_user(user, "Привет")

        self.assertTrue(ok)
        mocked_send.assert_called_once_with(999888, "Привет", reply_markup=None)
        self.assertEqual(len(mail.outbox), 1)


class TelegramProxySettingsTestCase(TestCase):
    """Прокси для requests при настройке TELEGRAM_API_PROXY_URL."""

    def test_proxies_dict_when_url_set(self) -> None:
        with override_settings(TELEGRAM_API_PROXY_URL="http://proxy.local:8080"):
            proxies = telegram_requests_proxies()

        self.assertEqual(
            proxies,
            {"http": "http://proxy.local:8080", "https": "http://proxy.local:8080"},
        )

    def test_proxies_none_when_empty(self) -> None:
        with override_settings(TELEGRAM_API_PROXY_URL=""):
            self.assertIsNone(telegram_requests_proxies())
