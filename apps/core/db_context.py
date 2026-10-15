"""Жизненный цикл соединения для фоновой и встроенной отправки уведомлений."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import close_old_connections, connection


@contextmanager
def background_connection() -> Iterator[None]:
    """Освобождает соединение задачи, не закрывая транзакцию вызывающего кода.

    В новом потоке Django создаёт отдельное соединение. При встроенном запуске
    из атомарной операции соединение принадлежит вызывающему коду: закрывать
    его до выхода из транзакции нельзя, в том числе при ошибке отправки.

    Yields:
        Контекст выполнения задачи с доступным соединением Django.
    """
    owns_connection = not connection.in_atomic_block
    if owns_connection:
        close_old_connections()
    try:
        yield
    finally:
        if owns_connection:
            connection.close()
