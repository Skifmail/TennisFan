"""Напоминания участникам спарринговых матчей о результате.

Запуск: python manage.py send_sparring_result_reminders

Команда отправляет напоминания за 3 дня, за 1 день, в день дедлайна и на
следующий день после него. Каждый этап фиксируется в ``SparringMatchReminder``,
поэтому повторный запуск не создаёт дублей. Турнирные матчи обрабатывает
``send_deadline_reminders``.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from apps.notifications.services import create_notification
from apps.sparring.models import SparringMatchReminder, SparringReminderStage
from apps.telegram_bot import notifications as tg
from apps.telegram_bot import services as bot_services
from apps.tournaments.management.commands.send_deadline_reminders import (
    _notify_match_participants,
)
from apps.tournaments.models import Match
from apps.tournaments.utils import get_match_participant_users

STAGE_BY_DAYS_LEFT: dict[int, str] = {
    3: SparringReminderStage.D3,
    1: SparringReminderStage.D1,
    0: SparringReminderStage.D0,
    -1: SparringReminderStage.OVERDUE1,
}


def _notify_overdue_participants(match: Match) -> None:
    """Напомнить участникам, что дедлайн спарринга прошёл вчера."""
    url = reverse("match_detail", args=[match.pk])
    text = (
        "Спарринг: дедлайн прошёл вчера. Внесите результат сегодня — "
        "через 2 дня матч будет отменён без изменения рейтинга."
    )
    telegram_text = (
        "⏰ <b>Дедлайн спарринга прошёл</b>\n\n"
        f"{match.get_player1_display()} — {match.get_player2_display()}\n\n"
        "Внесите результат сегодня. Через 2 дня матч будет отменён, "
        "рейтинг не изменится."
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "📝 Внести результат",
                    "callback_data": f"result_enter_{match.pk}",
                },
                {"text": "📅 Мои матчи", "callback_data": "menu_my_matches"},
            ]
        ]
    }
    for user in get_match_participant_users(match):
        create_notification(
            user=user,
            message=text,
            url=url,
            event_key="match.deadline_reminder",
        )
        if bot_services.is_configured():
            tg.send_to_user_by_user(
                user,
                telegram_text,
                reply_markup=reply_markup,
                skip_email=True,
                event_key="match.deadline_reminder",
            )


class Command(BaseCommand):
    """Отправить напоминания по этапам спаррингового матча."""

    help = (
        "Напомнить участникам спарринга о дедлайне за 3 дня, за 1 день, "
        "в день дедлайна и на следующий день. Дубли исключены."
    )

    def add_arguments(self, parser) -> None:
        """Аргументы CLI."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать матчи и этапы, ничего не отправлять.",
        )

    def handle(self, *args, **options) -> None:
        """Найти спарринги в окнах напоминаний и отправить сообщения."""
        dry_run = bool(options.get("dry_run"))
        today = timezone.localdate()
        matches = list(
            Match.objects.filter(
                match_type=Match.MatchType.SPARRING,
                deadline__isnull=False,
                status__in=(
                    Match.MatchStatus.SCHEDULED,
                    Match.MatchStatus.IN_PROGRESS,
                ),
            ).select_related("player1", "player2", "team1", "team2")
        )

        sent = 0
        for match in matches:
            assert match.deadline is not None
            days_left = (timezone.localtime(match.deadline).date() - today).days
            stage = STAGE_BY_DAYS_LEFT.get(days_left)
            if stage is None:
                continue
            if dry_run:
                self.stdout.write(
                    f"  матч #{match.pk}: этап {stage} (дней до дедлайна {days_left})"
                )
                sent += 1
                continue

            reminder, created = SparringMatchReminder.objects.get_or_create(
                match=match, stage=stage
            )
            if not created:
                continue
            try:
                if stage == SparringReminderStage.OVERDUE1:
                    _notify_overdue_participants(match)
                else:
                    _notify_match_participants(match, days_left=max(days_left, 0))
            except Exception:  # noqa: BLE001 - сбой одного матча не ломает cron
                reminder.delete()
                self.stderr.write(
                    f"Матч #{match.pk}: напоминание не отправлено, "
                    "повторим при следующем запуске."
                )
                continue
            sent += 1

        action = "к отправке" if dry_run else "отправлено"
        self.stdout.write(f"Напоминаний {action}: {sent}.")
