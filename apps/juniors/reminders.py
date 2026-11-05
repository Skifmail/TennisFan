"""Однократные напоминания семьям о назначенных детских матчах."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import JuniorMatch, JuniorTournament
from .notifications import notify_match


def send_match_reminders() -> int:
    """Уведомляет за сутки до игры; повторный запуск не дублирует сообщение."""
    now = timezone.now()
    ids = list(
        JuniorMatch.objects.filter(
            status="pending",
            tournament__status="active",
            entry1__status="confirmed",
            entry2__status="confirmed",
            entry1__child__status="active",
            entry2__child__status="active",
            scheduled_at__gt=now,
            scheduled_at__lte=now + timedelta(days=1),
            reminder_sent_at__isnull=True,
        ).values_list("pk", "tournament_id")
    )
    sent = 0
    for match_id, tournament_id in ids:
        # Тот же порядок блокировок, что при результате, переносе и отмене.
        with transaction.atomic():
            tournament = JuniorTournament.objects.select_for_update().get(
                pk=tournament_id
            )
            match = (
                JuniorMatch.objects.select_for_update(of=("self",))
                .select_related("entry1__child", "entry2__child", "tournament")
                .get(pk=match_id)
            )
            current = timezone.now()
            if (
                tournament.status != "active"
                or match.status != "pending"
                or match.reminder_sent_at is not None
                or match.scheduled_at is None
                or not current < match.scheduled_at <= current + timedelta(days=1)
                or not match.entry1
                or not match.entry2
                or any(
                    entry.status != "confirmed" or entry.child.status != "active"
                    for entry in (match.entry1, match.entry2)
                )
            ):
                continue
            local_time = timezone.localtime(match.scheduled_at).strftime(
                "%d.%m.%Y в %H:%M"
            )
            notify_match(
                match,
                f"Напоминаем о матче в «{tournament.name}»: {local_time}. Проверьте площадку и время. После игры внесите результат на странице матча.",
            )
            match.reminder_sent_at = current
            match.save(update_fields=["reminder_sent_at"])
            sent += 1
    return sent
