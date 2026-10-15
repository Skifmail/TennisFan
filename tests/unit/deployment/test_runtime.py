"""Проверки ограниченного ожидания и безопасного окружения фоновых задач."""

import json
from pathlib import Path

import pytest
from django.db import OperationalError
from pytest_mock import MockerFixture

from apps.core.deployment import (
    DeploymentError,
    save_cron_environment,
    wait_for_database,
)


def test_wait_for_database_when_available_should_return(mocker: MockerFixture) -> None:
    """Доступная БД не вызывает повторных подключений."""
    database = mocker.patch("apps.core.deployment.connection")
    wait_for_database(timeout=1)
    database.ensure_connection.assert_called_once_with()


def test_wait_for_database_when_temporarily_unavailable_should_retry(
    mocker: MockerFixture,
) -> None:
    """Временная недоступность не приводит к немедленному падению."""
    database = mocker.patch("apps.core.deployment.connection")
    database.ensure_connection.side_effect = [OperationalError("private"), None]
    sleep = mocker.patch("apps.core.deployment.time.sleep")
    wait_for_database(timeout=10)
    assert database.ensure_connection.call_count == 2
    sleep.assert_called_once()


def test_wait_for_database_when_deadline_exceeded_should_raise_without_secret(
    mocker: MockerFixture,
) -> None:
    """Истечение срока не раскрывает строку подключения в исключении."""
    database = mocker.patch("apps.core.deployment.connection")
    database.ensure_connection.side_effect = OperationalError("private-password")
    mocker.patch("apps.core.deployment.time.monotonic", side_effect=[0, 2])
    with pytest.raises(DeploymentError, match="время ожидания") as caught:
        wait_for_database(timeout=1)
    assert "private-password" not in str(caught.value)


@pytest.mark.parametrize("timeout", [0, -1])
def test_wait_for_database_when_invalid_timeout_should_raise(timeout: int) -> None:
    """Недопустимый срок отклоняется до подключения."""
    with pytest.raises(DeploymentError):
        wait_for_database(timeout=timeout)


def test_save_cron_environment_when_written_should_be_private_and_literal(
    tmp_path: Path,
) -> None:
    """Пароли сохраняются без shell-подстановок и доступны только владельцу."""
    target = tmp_path / "cron.env.json"
    environment = {"PASSWORD": "a$b'\"c", "UNICODE": "Казань"}
    save_cron_environment(target, environment)
    assert target.stat().st_mode & 0o777 == 0o600
    assert json.loads(target.read_text()) == environment
    assert list(tmp_path.iterdir()) == [target]
