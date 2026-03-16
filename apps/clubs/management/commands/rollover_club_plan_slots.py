"""CRON-команда переноса неиспользованных слотов клубных тарифов игроков."""

from __future__ import annotations

import logging
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.clubs.models import ClubMemberPlan, ClubMemberPlanStatus
from apps.clubs.plan_services import rollover_member_slots

logger = logging.getLogger(__name__)


def _get_default_source_period(today: date) -> tuple[int, int]:
    """Возвращает предыдущий календарный месяц.

    Args:
        today: Текущая дата.

    Returns:
        tuple[int, int]: Пара (год, месяц) предыдущего месяца.
    """
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def _get_next_period(year: int, month: int) -> tuple[int, int]:
    """Возвращает следующий календарный месяц.

    Args:
        year: Год исходного периода.
        month: Месяц исходного периода.

    Returns:
        tuple[int, int]: Пара (год, месяц) следующего периода.
    """
    if month == 12:
        return year + 1, 1
    return year, month + 1


class Command(BaseCommand):
    help = "Переносит остатки слотов по активным клубным тарифам в следующий месяц."

    def add_arguments(self, parser) -> None:
        """Добавляет аргументы командной строки.

        Args:
            parser: Парсер аргументов Django management command.

        Returns:
            None: Значение не возвращается.
        """
        parser.add_argument(
            "--year", type=int, required=False, help="Год исходного периода"
        )
        parser.add_argument(
            "--month", type=int, required=False, help="Месяц исходного периода (1-12)"
        )

    def handle(self, *args, **options) -> None:
        """Запускает перенос неиспользованных слотов в следующий месяц.

        Args:
            *args: Позиционные аргументы Django.
            **options: Именованные аргументы команды.

        Returns:
            None: Значение не возвращается.
        """
        year_opt = options.get("year")
        month_opt = options.get("month")
        if year_opt and month_opt:
            source_year, source_month = int(year_opt), int(month_opt)
        else:
            source_year, source_month = _get_default_source_period(timezone.localdate())

        if source_month < 1 or source_month > 12:
            self.stderr.write(self.style.ERROR("Месяц должен быть в диапазоне 1..12."))
            return

        target_year, target_month = _get_next_period(source_year, source_month)
        assignments = ClubMemberPlan.objects.select_related(
            "plan", "club_member"
        ).filter(
            status=ClubMemberPlanStatus.ACTIVE,
            plan__is_active=True,
            plan__allow_rollover_slots=True,
        )

        processed = 0
        rolled_total = 0
        with transaction.atomic():
            for assignment in assignments:
                rolled = rollover_member_slots(
                    assignment,
                    source_year=source_year,
                    source_month=source_month,
                    target_year=target_year,
                    target_month=target_month,
                )
                processed += 1
                rolled_total += rolled

        logger.info(
            "Club plan slot rollover completed. source=%04d-%02d target=%04d-%02d processed=%d rolled_total=%d",
            source_year,
            source_month,
            target_year,
            target_month,
            processed,
            rolled_total,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Обработано назначений: {processed}. Перенесено слотов: {rolled_total}."
            )
        )
