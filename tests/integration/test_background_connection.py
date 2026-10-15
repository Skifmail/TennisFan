"""Реальная транзакция и отдельный поток PostgreSQL не мешают друг другу."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, transaction

from apps.core.db_context import background_connection


@pytest.mark.django_db
def test_background_connection_when_called_inline_should_leave_transaction_usable() -> (
    None
):
    """Внешняя атомарная операция остаётся доступной после уведомления."""
    with transaction.atomic():
        physical = connection.connection
        with background_connection():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                assert cursor.fetchone() == (1,)
        assert connection.connection is physical
        with connection.cursor() as cursor:
            cursor.execute("SELECT 2")
            assert cursor.fetchone() == (2,)


@pytest.mark.django_db(transaction=True)
def test_background_connection_when_run_in_thread_should_close_owned_connection() -> (
    None
):
    """Завершившийся поток не удерживает подключение PostgreSQL."""
    if connection.vendor != "postgresql":
        pytest.skip("SQLite сохраняет in-memory соединение для тестового runner")

    def run() -> bool:
        with background_connection():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                assert cursor.fetchone() == (1,)
        return connection.connection is None

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(run).result(timeout=10)
