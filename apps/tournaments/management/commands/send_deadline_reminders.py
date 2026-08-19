"""
Напоминания о дедлайне матча за 2 и 1 день.

Выбирает матчи, дедлайн которых приходится на послезавтра или на завтра
(по календарным суткам локальной таймзоны), и отправляет участникам
напоминание: Telegram, личный кабинет и email. В тексте — предупреждение
о Walkover (−40) и инструкция, как отметить неявку соперника.

Окно считается по датам, а не по числу часов: дедлайны матчей приходятся
на границу суток, поэтому «ровно 24 часа» до них не наступает ни в один
из моментов ежедневного запуска.

Запуск: python manage.py send_deadline_reminders

Рекомендуется добавить в cron раз в день (например в 09:00):
  0 9 * * * cd /path && python manage.py send_deadline_reminders
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from apps.core.email_service import send_match_deadline_reminder_email
from apps.telegram_bot import notifications as tg
from apps.telegram_bot import services as bot_services
from apps.tournaments.models import Match
from apps.tournaments.utils import get_match_participant_users
from apps.users.models import Notification

logger = logging.getLogger(__name__)

LK_MESSAGE_MAX_LEN = 255


def matches_with_deadline_in_days(days_left: int) -> QuerySet[Match]:
    """Отобрать запланированные матчи с дедлайном через ``days_left`` суток.

    Args:
        days_left: Сколько календарных дней осталось до дедлайна (1 или 2).

    Returns:
        QuerySet матчей, дедлайн которых приходится на нужную дату.
    """
    target_date = timezone.localdate() + timedelta(days=days_left)
    day_start = timezone.make_aware(datetime.combine(target_date, time.min))
    day_end = day_start + timedelta(days=1)
    return Match.objects.filter(
        deadline__isnull=False,
        deadline__gte=day_start,
        deadline__lt=day_end,
        status=Match.MatchStatus.SCHEDULED,
    ).select_related("tournament", "player1", "player2", "team1", "team2")


def build_deadline_reminder_lk_message(
    *,
    tournament_name: str,
    days_left: int,
    deadline_str: str,
) -> str:
    """Текст напоминания в личный кабинет (лимит 255 символов).

    Args:
        tournament_name: Название турнира.
        days_left: Осталось дней (1 или 2).
        deadline_str: Дедлайн для отображения.

    Returns:
        Сообщение не длиннее ``LK_MESSAGE_MAX_LEN``.
    """
    left = "1 день" if days_left == 1 else f"{days_left} дня"
    msg = (
        f"Если матч не сыграют до дедлайна — RT 0:0, дальше проходит кто сильнее по рейтингу (рейтинг не меняется). "
        f"«{tournament_name}»: осталось {left} ({deadline_str}). "
        f"Матч срывает соперник? Внесите неявку в карточке матча."
    )
    if len(msg) > LK_MESSAGE_MAX_LEN:
        return msg[: LK_MESSAGE_MAX_LEN - 3] + "..."
    return msg


def _notify_match_participants(match: Match, days_left: int) -> None:
    """Отправить Telegram, ЛК и email участникам одного матча."""
    tg.notify_match_deadline_reminder(match, days_left=days_left)
    deadline_str = match.deadline.strftime("%d.%m.%Y %H:%M") if match.deadline else ""
    tournament_name = match.tournament.name if match.tournament_id else "матч"
    msg = build_deadline_reminder_lk_message(
        tournament_name=tournament_name,
        days_left=days_left,
        deadline_str=deadline_str,
    )
    url = reverse("my_matches")
    base_url = ""
    try:
        from apps.core.email_service import _get_site_base_url

        base_url = _get_site_base_url()
    except Exception:
        logger.exception("deadline reminder: base url")
    match_url = f"{base_url}{reverse('match_detail', args=[match.pk])}"
    for user in get_match_participant_users(match):
        try:
            Notification.objects.create(user=user, message=msg, url=url)
        except Exception as exc:
            logger.warning(
                "Notification deadline %sd user %s: %s",
                days_left,
                user.pk,
                exc,
            )
        try:
            send_match_deadline_reminder_email(
                user,
                match,
                days_left=days_left,
                match_url=match_url,
            )
        except Exception as exc:
            logger.warning(
                "Email deadline %sd user %s: %s",
                days_left,
                user.pk,
                exc,
            )


class Command(BaseCommand):
    """Напоминания о дедлайне: Telegram, ЛК и email, со штрафом за неявку."""

    help = (
        "Отправить напоминания о дедлайне матча за 2 и 1 день "
        "(Telegram, ЛК, email) с предупреждением о Walkover (−40)."
    )

    def add_arguments(self, parser) -> None:
        """Аргументы CLI."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Не отправлять сообщения, только вывести матчи.",
        )

    def handle(self, *args, **options) -> None:
        """Найти матчи в окнах 2 дня / 1 день и уведомить участников."""
        dry_run = options.get("dry_run", False)
        telegram_ok = bot_services.is_configured()
        if not dry_run and not telegram_ok:
            self.stdout.write(
                "Telegram user bot не настроен — "
                "напоминания в Telegram пропущены, ЛК и email будут отправлены."
            )

        matches_2d = list(matches_with_deadline_in_days(2))
        matches_1d = list(matches_with_deadline_in_days(1))

        sent_2 = 0
        sent_1 = 0
        for match in matches_2d:
            if dry_run:
                self.stdout.write(
                    f"  [2d] Матч #{match.pk} {match} дедлайн {match.deadline}"
                )
                continue
            try:
                _notify_match_participants(match, days_left=2)
                sent_2 += 1
            except Exception as exc:
                logger.exception(
                    "send_deadline_reminder 2d match %s: %s", match.pk, exc
                )
        for match in matches_1d:
            if dry_run:
                self.stdout.write(
                    f"  [1d] Матч #{match.pk} {match} дедлайн {match.deadline}"
                )
                continue
            try:
                _notify_match_participants(match, days_left=1)
                sent_1 += 1
            except Exception as exc:
                logger.exception(
                    "send_deadline_reminder 1d match %s: %s", match.pk, exc
                )

        if dry_run:
            self.stdout.write(
                f"Dry-run: матчей за 2 дня: {len(matches_2d)}, "
                f"за 1 день: {len(matches_1d)}"
            )
            return
        self.stdout.write(
            f"Напоминаний отправлено: за 2 дня — {sent_2}, за 1 день — {sent_1}."
        )
