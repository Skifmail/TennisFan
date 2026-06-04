"""Юнит-тесты: доступ в закрытый Telegram-чат."""

from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.telegram_bot.private_chat import get_private_chat_access_status


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
