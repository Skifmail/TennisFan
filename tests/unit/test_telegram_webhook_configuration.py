"""Безопасная настройка действующего webhook и одинаковое окружение всех процессов."""

from __future__ import annotations

import os
import runpy
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import Any, Final
from unittest.mock import MagicMock, patch

import pytest
import requests
import yaml
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

ROOT: Final = Path(__file__).resolve().parents[2]
EXPECTED_ENVIRONMENT: Final = {
    "TELEGRAM_BOT_TOKEN": "${TELEGRAM_BOT_TOKEN:-}",
    "TELEGRAM_USER_BOT_TOKEN": "${TELEGRAM_USER_BOT_TOKEN:-}",
    "TELEGRAM_USER_BOT_USERNAME": "${TELEGRAM_USER_BOT_USERNAME:-}",
    "TELEGRAM_USER_BOT_WEBHOOK_SECRET": "${TELEGRAM_USER_BOT_WEBHOOK_SECRET:-}",
    "TELEGRAM_ADMIN_CHAT_ID": "${TELEGRAM_ADMIN_CHAT_ID:-}",
    "TELEGRAM_BOT_SITE_BASE_URL": "${TELEGRAM_BOT_SITE_BASE_URL:-}",
    "TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED": "${TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED:-False}",
    "TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID": "${TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID:-}",
    "TELEGRAM_PUBLIC_COMMUNITY_CHAT_ID": "${TELEGRAM_PUBLIC_COMMUNITY_CHAT_ID:-}",
    "TELEGRAM_PUBLIC_COMMUNITY_URL": "${TELEGRAM_PUBLIC_COMMUNITY_URL:-https://t.me/TennisFanu}",
    "TELEGRAM_ENABLED": "${TELEGRAM_ENABLED:-True}",
    "TELEGRAM_API_PROXY_URL": "${TELEGRAM_API_PROXY_URL:-}",
}


@pytest.fixture
def telegram_post() -> Iterator[MagicMock]:
    """Перехватить всю сеть команды с настоящим общим HTTP-транспортом."""
    with (
        override_settings(
            TELEGRAM_BOT_TOKEN="100:test-admin-token",
            TELEGRAM_USER_BOT_TOKEN="200:test-user-token",
            TELEGRAM_USER_BOT_WEBHOOK_SECRET="test-webhook-secret_123",
            TELEGRAM_BOT_SITE_BASE_URL="https://tennisfan.ru",
            TELEGRAM_API_PROXY_URL="",
            TELEGRAM_SUPPORT_BOT_TOKEN="300:unused-legacy-token",
        ),
        patch.dict(os.environ, {"TELEGRAM_ENABLED": "true"}),
        patch("requests.post") as post,
        patch("requests.get") as get,
    ):
        response = MagicMock(spec=requests.Response)
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": True}
        response.headers = {}
        post.return_value = response
        yield post
        get.assert_not_called()


def _run(**options: Any) -> str:
    """Вернуть только безопасный вывод команды без печати настроек и секретов."""
    output = StringIO()
    call_command("set_telegram_webhooks", stdout=output, stderr=output, **options)
    return output.getvalue()


def test_webhook_setup_posts_secret_and_only_supported_updates(
    telegram_post: MagicMock,
) -> None:
    """Секрет передаётся в JSON, список update явный, старые update не сбрасываются."""
    output = _run()
    assert telegram_post.call_count == 2
    calls = telegram_post.call_args_list
    assert calls[0].args[0].endswith("/deleteWebhook")
    assert calls[0].kwargs["json"] == {"drop_pending_updates": False}
    assert calls[1].args[0].endswith("/setWebhook")
    assert "?" not in calls[1].args[0]
    assert calls[1].kwargs["json"] == {
        "url": "https://tennisfan.ru/telegram/user-bot-webhook/",
        "secret_token": "test-webhook-secret_123",
        "allowed_updates": ["message", "callback_query", "chat_join_request"],
        "drop_pending_updates": False,
    }
    assert "support-webhook" not in output
    assert "test-webhook-secret" not in output
    assert "test-user-token" not in output
    assert "test-admin-token" not in output


@pytest.mark.parametrize("dry_run", [False, True])
def test_missing_user_secret_fails_before_changing_any_bot(
    telegram_post: MagicMock, dry_run: bool
) -> None:
    """Предварительная проверка не сбрасывает даже админский webhook при ошибке секрета."""
    with (
        override_settings(TELEGRAM_USER_BOT_WEBHOOK_SECRET=""),
        pytest.raises(CommandError, match="TELEGRAM_USER_BOT_WEBHOOK_SECRET"),
    ):
        _run(dry_run=dry_run)
    telegram_post.assert_not_called()


@pytest.mark.parametrize(
    "secret", ["with space", "кириллица", "x" * 257, "secret?value"]
)
def test_invalid_webhook_secret_is_rejected_without_echoing_it(
    telegram_post: MagicMock, secret: str
) -> None:
    """Telegram принимает только 1–256 ASCII-символов из A-Z, a-z, 0-9, _ и -."""
    with (
        override_settings(TELEGRAM_USER_BOT_WEBHOOK_SECRET=secret),
        pytest.raises(CommandError) as error,
    ):
        _run()
    assert secret not in str(error.value)
    telegram_post.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://tennisfan.ru",
        "https://user:secret@tennisfan.ru",
        "https://tennisfan.ru?secret=1",
        "https://tennisfan.ru/#secret",
        "https://tennisfan.ru/prefix",
        "https://tennisfan.ru:broken",
    ],
)
def test_webhook_origin_requires_https_without_credentials_query_or_path(
    telegram_post: MagicMock, url: str
) -> None:
    """Не выводить секреты из URL и не регистрировать несуществующий путь webhook."""
    with (
        override_settings(TELEGRAM_BOT_SITE_BASE_URL=url),
        pytest.raises(CommandError, match="TELEGRAM_BOT_SITE_BASE_URL"),
    ):
        _run()
    telegram_post.assert_not_called()


@pytest.mark.parametrize(
    "admin_token", ["200:test-user-token", "200:another-token-of-same-bot"]
)
def test_shared_user_and_admin_bot_is_rejected_before_delete_webhook(
    telegram_post: MagicMock, admin_token: str
) -> None:
    """Один и тот же бот не должен одновременно сбрасывать и устанавливать свой webhook."""
    with override_settings(TELEGRAM_BOT_TOKEN=admin_token), pytest.raises(CommandError):
        _run()
    telegram_post.assert_not_called()


def test_dry_run_validates_and_shows_plan_even_with_api_disabled(
    telegram_post: MagicMock,
) -> None:
    """Офлайн-проверка показывает URL без запроса к сети и без раскрытия секрета."""
    with patch.dict(os.environ, {"TELEGRAM_ENABLED": "False"}):
        output = _run(dry_run=True)
    assert "https://tennisfan.ru/telegram/user-bot-webhook/" in output
    assert "test-webhook-secret" not in output
    telegram_post.assert_not_called()


def test_disabled_api_skips_all_network(telegram_post: MagicMock) -> None:
    """Глобальное отключение не меняет webhook и не требует рабочей сети."""
    with patch.dict(os.environ, {"TELEGRAM_ENABLED": "False"}):
        assert "отключён" in _run()
    telegram_post.assert_not_called()


def test_admin_only_does_not_require_site_origin(telegram_post: MagicMock) -> None:
    """Бот только для исходящих админских сообщений не имеет входящего URL."""
    with override_settings(TELEGRAM_USER_BOT_TOKEN="", TELEGRAM_BOT_SITE_BASE_URL=""):
        _run()
    telegram_post.assert_called_once()
    assert telegram_post.call_args.args[0].endswith("/deleteWebhook")


def test_api_rejection_fails_command_instead_of_claiming_success(
    telegram_post: MagicMock,
) -> None:
    """HTTP 200 с ok=false не считается успешной настройкой."""
    telegram_post.return_value.json.return_value = {"ok": False, "error_code": 401}
    with pytest.raises(CommandError, match="telegram_unauthorized"):
        _run()


def test_api_timeout_error_does_not_expose_token_or_webhook_secret(
    telegram_post: MagicMock,
) -> None:
    """requests-исключение с секретным URL заменяется безопасным кодом ошибки."""
    telegram_post.side_effect = requests.ReadTimeout(
        "https://api.telegram.org/bot200:test-user-token/setWebhook?secret_token=test-webhook-secret_123"
    )
    with pytest.raises(CommandError) as error:
        _run()
    assert "test-user-token" not in str(error.value)
    assert "test-webhook-secret" not in str(error.value)


@pytest.mark.parametrize(
    "filename", ["docker-compose.yml", "docker-compose.dokploy.yml"]
)
def test_compose_forwards_webhook_and_community_settings_to_all_app_workers(
    filename: str,
) -> None:
    """Каждый процесс получает тот же секрет, username, ID и явный выключенный флаг доступа."""
    configuration = yaml.safe_load((ROOT / filename).read_text())
    for service in ("web", "chat", "cron", "release"):
        if service not in configuration["services"]:
            continue
        environment = configuration["services"][service]["environment"]
        for key, value in EXPECTED_ENVIRONMENT.items():
            assert environment.get(key) == value, f"{filename}: {service}.{key}"


@pytest.mark.parametrize(
    "flag, expected",
    [
        ("", False),
        ("False", False),
        ("0", False),
        ("true", True),
        ("1", True),
        (" YES ", True),
    ],
)
def test_settings_load_separate_community_ids_and_explicit_access_flag(
    monkeypatch: pytest.MonkeyPatch, flag: str, expected: bool
) -> None:
    """Загрузка окружения не включает закрытый чат неявно и не смешивает два ID."""
    monkeypatch.setenv("SECRET_KEY", "configuration-test-only")
    monkeypatch.setenv("DEBUG", "False")
    monkeypatch.setenv("TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED", flag)
    monkeypatch.setenv("TELEGRAM_PUBLIC_COMMUNITY_CHAT_ID", " -100123 ")
    monkeypatch.setenv("TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID", " -100456 ")
    monkeypatch.setenv("TELEGRAM_USER_BOT_WEBHOOK_SECRET", " normalized-secret ")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    values = runpy.run_path(str(ROOT / "config/settings.py"))
    assert values.get("TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED") is expected
    assert values.get("TELEGRAM_PUBLIC_COMMUNITY_CHAT_ID") == "-100123"
    assert values.get("TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID") == "-100456"
    assert values.get("TELEGRAM_USER_BOT_WEBHOOK_SECRET") == "normalized-secret"
