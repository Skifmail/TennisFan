"""Отдельные соединения и наблюдаемые блокировки для регрессий PostgreSQL."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from queue import Queue
from threading import Barrier
from time import monotonic, sleep
from typing import TypeVar

from django.db import connection

from apps.core.db_context import background_connection

T = TypeVar("T")


def on_connection(
    action: Callable[[], T],
    *,
    pid: Queue[int] | None = None,
    start: Barrier | None = None,
) -> T:
    """Выполнить действие на соединении потока с ограниченным временем ожидания БД."""
    with background_connection():
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '12s'")
            cursor.execute("SET lock_timeout = '10s'")
            cursor.execute("SELECT pg_backend_pid()")
            backend_pid = int(cursor.fetchone()[0])
        if pid is not None:
            pid.put(backend_pid)
        if start is not None:
            start.wait(timeout=10)
        return action()


def is_blocked(future: Future[T], pid: int, *, timeout: float = 5) -> bool:
    """Дождаться реального ожидания блокировки; быстрый успешный запрос означает гонку."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_blocking_pids(%s)", [pid])
            blockers = cursor.fetchone()[0]
        if blockers:
            return True
        if future.done():
            future.result()
            return False
        sleep(0.01)
    raise AssertionError("Запрос не завершился и не ждёт блокировки PostgreSQL.")
