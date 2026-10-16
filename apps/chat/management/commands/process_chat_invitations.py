"""Ограниченная фоновая обработка приглашений через существующий cron."""

from typing import Any

from django.core.management.base import BaseCommand

from apps.chat.invitations import process_invitation_queue
from apps.core.db_context import background_connection


class Command(BaseCommand):
    """Создаёт внутренние уведомления и передаёт ограниченный пакет писем SMTP.

    Attributes:
        help: Назначение задачи в списке команд.
    """

    help = "Обработать очередной пакет приглашений в городские чаты"

    def handle(self, *args: Any, **options: Any) -> None:
        """Выполняет порцию работы, освобождая принадлежащее задаче соединение.

        Args:
            args: Служебные позиционные аргументы.
            options: Служебные параметры Django.
        """
        with background_connection():
            process_invitation_queue()
