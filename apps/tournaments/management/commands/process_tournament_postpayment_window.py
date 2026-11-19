"""Обработка окон постоплаты турниров."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.tournaments.models import Tournament, TournamentStatus
from apps.tournaments.postpayment import (
    PostpaymentWindowError,
    finalize_postpayment_window,
    get_pending_postpayment_invoices,
    get_postpayment_progress,
    open_postpayment_window,
    send_1h_reminders,
    settle_postpayment_with_available_fancoin,
    sync_postpayment_invoices_deadline,
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
        # Авто-открытие окна постоплаты: за N часов до старта турнира
        # (N = postpayment_deadline_hours), если старт известен.
        auto_opened = 0
        candidates = Tournament.objects.filter(
            allow_postpayment=True,
            postpayment_window_started_at__isnull=True,
            bracket_generated=False,
            start_date__isnull=False,
            start_time__isnull=False,
        ).exclude(
            status__in=(TournamentStatus.CANCELLED, TournamentStatus.COMPLETED),
        )
        for tournament in candidates:
            start_at = tournament.get_start_datetime()
            if start_at is None or start_at <= now:
                continue
            hours = tournament.get_postpayment_deadline_hours()
            open_at = start_at - timedelta(hours=hours)
            if now < open_at:
                continue
            try:
                open_postpayment_window(tournament)
            except PostpaymentWindowError as exc:
                self.stderr.write(
                    f"Не открыто окно постоплаты турнира {tournament.pk}: {exc}"
                )
                continue
            auto_opened += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"Авто-открыто окно постоплаты: {tournament.slug}"
                )
            )
        processed = 0
        tournaments = Tournament.objects.filter(
            postpayment_window_started_at__isnull=False,
        ).distinct()
        for tournament in tournaments:
            # Подтянуть due_at из postpayment_deadline_hours (если админ продлил окно).
            sync_postpayment_invoices_deadline(tournament)
            progress = get_postpayment_progress(tournament)
            is_completed = bool(progress["completed"])
            has_expired_pending = (
                get_pending_postpayment_invoices(tournament)
                .filter(
                    due_at__lte=now,
                )
                .exists()
            )
            if is_completed or has_expired_pending:
                finalize_postpayment_window(tournament)
                processed += 1
        self.stdout.write(
            self.style.SUCCESS(
                "Postpayment processed: "
                f"fancoin_settled={fancoin_settled}, "
                f"reminders={reminders_sent}, finalized={processed}, "
                f"auto_opened={auto_opened}"
            )
        )
