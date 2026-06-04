"""Юнит-тесты: разбор TELEGRAM_ENABLED."""

from __future__ import annotations

import os
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.telegram_bot.telegram_http import is_telegram_api_enabled


class TelegramApiEnabledParsingTests(SimpleTestCase):
    """Проверка разбора TELEGRAM_ENABLED (окружение имеет приоритет над settings)."""

    def test_env_false_disables_even_if_settings_true(self) -> None:
        """Строка false в окружении отключает API независимо от settings."""
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "false"}, clear=False):
            with override_settings(TELEGRAM_ENABLED=True):
                self.assertFalse(is_telegram_api_enabled())

    def test_env_true_enables(self) -> None:
        """Строка true в окружении включает API."""
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "true"}, clear=False):
            with override_settings(TELEGRAM_ENABLED=False):
                self.assertTrue(is_telegram_api_enabled())

    def test_env_empty_string_disables(self) -> None:
        """Пустая строка в окружении отключает API."""
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
