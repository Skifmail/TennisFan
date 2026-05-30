"""Обработка окон постоплаты турниров."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.tournaments.models import Tournament, TournamentPostpaymentInvoice
from apps.tournaments.postpayment import (
    finalize_postpayment_window,
    get_postpayment_progress,
    send_1h_reminders,
    settle_postpayment_with_available_fancoin,
)


class Command(BaseCommand):
    """Cron-команда обработки постоплаты турниров."""

    help = (
        "Списывает FT при появлении баланса, отправляет напоминания по постоплате, "
        "закрывает истёкшие окна и формирует сетки после оплаты."
    )

    def handle(self, *args, **options):
        """Выполнить обработку активных окон постоплаты.

        Args:
            *args: Позиционные аргументы команды.
            **options: Именованные аргументы команды.

        Returns:
            None: Результат выводится в stdout.
        """
        fancoin_settled = settle_postpayment_with_available_fancoin()
        reminders_sent = send_1h_reminders()
        now = timezone.now()
        processed = 0
        tournaments = Tournament.objects.filter(
            postpayment_window_started_at__isnull=False,
            bracket_generated=False,
        )
        for tournament in tournaments:
            progress = get_postpayment_progress(tournament)
            is_completed = bool(progress["completed"])
            has_expired_pending = TournamentPostpaymentInvoice.objects.filter(
                tournament=tournament,
                status=TournamentPostpaymentInvoice.Status.PENDING,
                due_at__lte=now,
            ).exists()
            if is_completed or has_expired_pending:
                finalize_postpayment_window(tournament)
                processed += 1
        self.stdout.write(
            self.style.SUCCESS(
                "Postpayment processed: "
                f"fancoin_settled={fancoin_settled}, "
                f"reminders={reminders_sent}, finalized={processed}"
            )
        )
