"""Контракт владения соединением фоновой задачи."""

import pytest
from pytest_mock import MockerFixture

from apps.core.db_context import background_connection


def test_background_connection_when_parent_transaction_active_should_preserve_connection(
    mocker: MockerFixture,
) -> None:
    """Встроенный запуск не закрывает соединение внешней транзакции."""
    database = mocker.patch("apps.core.db_context.connection")
    database.in_atomic_block = True
    cleanup = mocker.patch("apps.core.db_context.close_old_connections")
    with background_connection():
        database.cursor()
    cleanup.assert_not_called()
    database.close.assert_not_called()


def test_background_connection_when_worker_finishes_should_cleanup(
    mocker: MockerFixture,
) -> None:
    """Выделенный поток освобождает своё соединение после успешной отправки."""
    database = mocker.patch("apps.core.db_context.connection")
    database.in_atomic_block = False
    cleanup = mocker.patch("apps.core.db_context.close_old_connections")
    with background_connection():
        database.cursor()
    cleanup.assert_called_once_with()
    database.close.assert_called_once_with()


def test_background_connection_when_worker_raises_should_cleanup(
    mocker: MockerFixture,
) -> None:
    """Ошибка задачи не оставляет соединение открытым."""
    database = mocker.patch("apps.core.db_context.connection")
    database.in_atomic_block = False
    mocker.patch("apps.core.db_context.close_old_connections")
    with pytest.raises(ValueError, match="test-only"):
        with background_connection():
            raise ValueError("test-only")
    database.close.assert_called_once_with()
