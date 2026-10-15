"""Единственный ограниченный по времени подготовительный этап web-контейнера."""

from __future__ import annotations

import os
import time
from typing import Any, Final

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from loguru import logger

from apps.core.deployment import (
    DATABASE_RETRY_SECONDS,
    DATABASE_WAIT_SECONDS,
    DeploymentError,
    wait_for_database,
)

MIGRATION_LOCK_ID: Final = 87352016
MIGRATION_DDL_LOCK_TIMEOUT_SECONDS: Final = 15


class Command(BaseCommand):
    """Подготавливает БД, сериализуя миграции PostgreSQL advisory-блокировкой.

    Attributes:
        help: Описание команды в manage.py.
    """

    help = "Ожидает БД и применяет миграции перед запуском web"

    def handle(self, *args: Any, **options: Any) -> None:
        """Подготавливает БД и опционально первого администратора.

        Args:
            args: Позиционные аргументы Django.
            options: Опции команды Django.

        Raises:
            CommandError: БД или блокировка недоступна, миграция не выполнена.
        """
        try:
            wait_for_database()
        except DeploymentError as exc:
            raise CommandError(str(exc)) from None
        locked = False
        try:
            if connection.vendor == "postgresql":
                deadline = time.monotonic() + DATABASE_WAIT_SECONDS
                while not locked:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_try_advisory_lock(%s)", [MIGRATION_LOCK_ID]
                        )
                        locked = bool(cursor.fetchone()[0])
                    if not locked:
                        if time.monotonic() >= deadline:
                            raise CommandError(
                                "Истекло время ожидания блокировки миграций"
                            )
                        time.sleep(DATABASE_RETRY_SECONDS)
                # Не блокируем боевые запросы надолго при конфликте DDL с трафиком.
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SET lock_timeout = %s",
                        [f"{MIGRATION_DDL_LOCK_TIMEOUT_SECONDS}s"],
                    )
            call_command("migrate", interactive=False)
            email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")
            password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
            if email and password:
                user_model = get_user_model()
                if not user_model.objects.filter(is_superuser=True).exists():
                    user_model.objects.create_superuser(email=email, password=password)
                    logger.info("Создан первый администратор")
            logger.info("Подготовка базы данных завершена")
        finally:
            if locked:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [MIGRATION_LOCK_ID])
