"""
Management-команда для отправки напоминаний о членских взносах (за 7 и 3 дня до конца периода)
и уведомлений о просрочке.
"""

import logging
from datetime import date

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    ClubFeePayment,
    ClubMember,
    ClubMembershipFee,
    ClubMemberStatus,
    FeePeriod,
)
from apps.clubs.notifications import (
    send_debtors_summary,
    send_fee_overdue_notifications,
    send_fee_reminder_notifications,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Отправка напоминаний о членских взносах игрокам клубов и сводки должников админам."""

    help = "Отправляет напоминания и уведомления о просрочке членского взноса."

    def handle(self, *args, **options):
        """Основная логика команды."""
        today = timezone.now().date()
        sent_reminders = 0
        sent_overdue = 0

        fees = ClubMembershipFee.objects.filter(is_active=True).select_related("club")
        for fee in fees:
            club = fee.club
            members = ClubMember.objects.filter(
                club=club, status=ClubMemberStatus.ACTIVE
            ).select_related("user")

            debtors_list: list[dict[str, str]] = []

            for member in members:
                period_label = self._get_current_period_label(fee, today)
                paid = ClubFeePayment.objects.filter(
                    member=member, fee=fee, period_label=period_label
                ).exists()
                if paid:
                    continue

                days_left = self._days_until_period_end(fee, today)

                if days_left in (7, 3):
                    send_fee_reminder_notifications(
                        club=club,
                        member=member,
                        days_left=days_left,
                        amount=fee.amount,
                        period_label=period_label,
                    )
                    sent_reminders += 1
                elif days_left < 0:
                    send_fee_overdue_notifications(
                        club=club,
                        member=member,
                        amount=fee.amount,
                        period_label=period_label,
                        restrict_access=fee.restrict_tournament_access,
                    )
                    sent_overdue += 1

                if days_left <= 0:
                    debtors_list.append(
                        {
                            "name": member.user.get_full_name() or member.user.email,
                            "email": member.user.email,
                        }
                    )

            if debtors_list:
                try:
                    fees_url = reverse(
                        "clubs:fees_payments", kwargs={"slug": club.slug}
                    )
                except Exception:
                    fees_url = ""
                period_label = self._get_current_period_label(fee, today)
                send_debtors_summary(
                    club=club,
                    period_label=period_label,
                    debtors=debtors_list,
                    fees_url=fees_url,
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Напоминания отправлены: {sent_reminders}. "
                f"Уведомления о просрочке: {sent_overdue}."
            )
        )
        logger.info(
            "send_club_fee_reminders: reminders=%d, overdue=%d",
            sent_reminders,
            sent_overdue,
        )

    def _get_current_period_label(self, fee: ClubMembershipFee, today: date) -> str:
        """Метка текущего периода для настройки взноса."""
        if fee.period == FeePeriod.MONTHLY:
            return today.strftime("%Y-%m")
        if fee.period == FeePeriod.QUARTERLY:
            q = (today.month - 1) // 3 + 1
            return f"{today.year}-Q{q}"
        if fee.period == FeePeriod.YEARLY:
            return str(today.year)
        return today.strftime("%Y-%m")

    def _days_until_period_end(self, fee: ClubMembershipFee, today: date) -> int:
        """Количество дней до конца текущего периода."""
        if fee.period == FeePeriod.MONTHLY:
            from calendar import monthrange

            _, last_day = monthrange(today.year, today.month)
            return last_day - today.day
        if fee.period == FeePeriod.YEARLY:
            year_end = date(today.year, 12, 31)
            return (year_end - today).days
        if fee.period == FeePeriod.QUARTERLY:
            from calendar import monthrange

            q = (today.month - 1) // 3 + 1
            quarter_end_month = q * 3
            _, last_day = monthrange(today.year, quarter_end_month)
            quarter_end = date(today.year, quarter_end_month, last_day)
            return (quarter_end - today).days
        return 0
