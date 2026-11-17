"""Отменить спарринги, результат которых не внесли через 3 дня после дедлайна.

Запуск: python manage.py sparring_process_overdue_matches

Матч переводится в ``cancelled``, рейтинг помечается как ``NOT_APPLICABLE``:
просроченный спарринг не должен менять силу игроков. Участники получают
уведомление в личном кабинете и Telegram.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.notifications.services import create_notification
from apps.telegram_bot import notifications as tg
from apps.telegram_bot import services as bot_services
from apps.tournaments.models import Match
from apps.tournaments.utils import get_match_participant_users

OVERDUE_CANCEL_DAYS = 3


class Command(BaseCommand):
    """Закрыть просроченные спарринги без изменения рейтинга."""

    help = (
        "Отменить спарринги, результат которых не внесён через "
        f"{OVERDUE_CANCEL_DAYS} дня после дедлайна, без изменения рейтинга."
    )

    def add_arguments(self, parser) -> None:
        """Аргументы CLI."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать матчи, ничего не менять.",
        )

    def handle(self, *args, **options) -> None:
        """Найти просроченные спарринги и отменить их."""
        dry_run = bool(options.get("dry_run"))
        cutoff = timezone.now() - timedelta(days=OVERDUE_CANCEL_DAYS)
        matches = list(
            Match.objects.filter(
                match_type=Match.MatchType.SPARRING,
                deadline__isnull=False,
                deadline__lte=cutoff,
                status__in=(
                    Match.MatchStatus.SCHEDULED,
                    Match.MatchStatus.IN_PROGRESS,
                ),
            ).select_related("player1", "player2", "team1", "team2")
        )

        if dry_run:
            for match in matches:
                self.stdout.write(
                    f"  матч #{match.pk}: дедлайн {match.deadline}, будет отменён"
                )
            self.stdout.write(f"Dry-run: к отмене — {len(matches)}.")
            return

        cancelled = 0
        for match in matches:
            try:
                with transaction.atomic():
                    fresh = Match.objects.select_for_update().get(pk=match.pk)
                    if (
                        fresh.match_type != Match.MatchType.SPARRING
                        or fresh.deadline is None
                        or fresh.deadline > cutoff
                        or fresh.status
                        not in (
                            Match.MatchStatus.SCHEDULED,
                            Match.MatchStatus.IN_PROGRESS,
                        )
                    ):
                        continue
                    fresh.status = Match.MatchStatus.CANCELLED
                    fresh.rating_status = Match.RatingCalcStatus.NOT_APPLICABLE
                    fresh.save(update_fields=["status", "rating_status"])
                    self._notify_participants(fresh)
                cancelled += 1
            except Exception as exc:  # noqa: BLE001 - один матч не ломает cron
                self.stderr.write(f"Матч #{match.pk}: не удалось отменить: {exc}")
        self.stdout.write(f"Отменено просроченных спаррингов: {cancelled}.")

    def _notify_participants(self, match: Match) -> None:
        """Сообщить участникам об отмене и отсутствии влияния на рейтинг."""
        url = reverse("match_detail", args=[match.pk])
        text = (
            "Спарринг отменён: результат не внесён в течение "
            f"{OVERDUE_CANCEL_DAYS} дней после дедлайна. Рейтинг не изменился. "
            "Создайте новую заявку, чтобы сыграть ещё раз."
        )
        telegram_text = (
            "⌛ <b>Спарринг отменён</b>\n\n"
            f"{match.get_player1_display()} — {match.get_player2_display()}\n\n"
            f"Результат не внесён в течение {OVERDUE_CANCEL_DAYS} дней после "
            "дедлайна. Рейтинг не изменился."
        )
        for user in get_match_participant_users(match):
            create_notification(
                user=user,
                message=text,
                url=url,
                event_key="match.overdue_result",
            )
            if bot_services.is_configured():
                tg.send_to_user_by_user(
                    user,
                    telegram_text,
                    skip_email=True,
                    event_key="match.overdue_result",
                )
