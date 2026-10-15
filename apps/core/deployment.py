"""Безопасные операции подготовки контейнеров к запуску."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from django.db import OperationalError, connection
from loguru import logger

DATABASE_WAIT_SECONDS: Final = 120
DATABASE_RETRY_SECONDS: Final = 2


class DeploymentError(RuntimeError):
    """Ошибка подготовки приложения к запуску.

    Attributes:
        args: Безопасное описание без реквизитов подключения.
    """


def wait_for_database(*, timeout: int = DATABASE_WAIT_SECONDS) -> None:
    """Ожидает доступность БД в пределах заданного срока.

    Args:
        timeout: Максимальное время ожидания в секундах.

    Raises:
        DeploymentError: Срок недопустим или БД не стала доступна вовремя.
    """
    if timeout <= 0:
        raise DeploymentError("Срок ожидания должен быть положительным")
    deadline = time.monotonic() + timeout
    while True:
        try:
            connection.ensure_connection()
            return
        except OperationalError:
            connection.close()
            if time.monotonic() >= deadline:
                raise DeploymentError("Истекло время ожидания базы данных") from None
            logger.warning("База данных пока недоступна; повтор подключения")
            time.sleep(DATABASE_RETRY_SECONDS)


def save_cron_environment(path: Path, environment: Mapping[str, str]) -> None:
    """Атомарно сохраняет окружение с закрытыми правами с момента создания.

    Args:
        path: Путь к файлу окружения cron.
        environment: Значения без shell-интерпретации.

    Raises:
        OSError: Не удалось создать либо заменить файл.
    """
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(dict(environment), temporary, ensure_ascii=False)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
