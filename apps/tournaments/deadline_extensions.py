"""Запросы продления дедлайна с общей блокировкой матча и истории обработки."""

from __future__ import annotations

from datetime import timedelta
from html import escape

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.users.models import Player

from .match_locking import lock_match
from .models import DeadlineExtensionRequest, Match
from .utils import get_match_player_side


def _notify_requested(extension_id: int) -> None:
    """Сообщить администратору о сохранённом запросе, не переживающем откат БД."""
    from apps.core.telegram_notify import send_admin_message
    from apps.telegram_bot.notifications import _get_site_base_url

    extension = (
        DeadlineExtensionRequest.objects.select_related(
            "match__tournament", "requested_by__user"
        )
        .filter(pk=extension_id)
        .first()
    )
    if extension is None or extension.status != DeadlineExtensionRequest.Status.PENDING:
        return
    match = extension.match
    tournament = match.tournament
    context = tournament.name if tournament is not None else "Спарринг"
    admin_url = _get_site_base_url().rstrip("/") + reverse(
        "admin:telegram_bot_deadlineextensionrequestproxy_changelist"
    )
    deadline = (
        timezone.localtime(match.deadline).strftime("%d.%m.%Y %H:%M")
        if match.deadline
        else "—"
    )
    send_admin_message(
        "🔄 <b>Запрос на продление дедлайна</b>\n\n"
        f"Игрок: {escape(str(extension.requested_by))}\n"
        f"Матч №{match.pk}: {escape(context)}\n"
        f"{escape(match.get_player1_display())} — {escape(match.get_player2_display())}\n"
        f"Текущий дедлайн: {deadline}\n\n"
        f'<a href="{escape(admin_url, quote=True)}">Список запросов в админке</a>'
    )


@transaction.atomic
def request_deadline_extension(
    match_id: int, requested_by: Player
) -> tuple[DeadlineExtensionRequest, bool]:
    """Сохранить не более одного ожидающего запроса от свежего участника матча."""
    match = lock_match(match_id)
    if (
        match is None
        or match.status
        not in (Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS)
        or not match.deadline
    ):
        raise ValidationError("Матч не найден, завершён или отменён.")
    if get_match_player_side(match, requested_by) is None:
        raise ValidationError("Вы больше не участвуете в этом матче.")
    existing = DeadlineExtensionRequest.objects.filter(
        match=match,
        requested_by=requested_by,
        status=DeadlineExtensionRequest.Status.PENDING,
    ).first()
    if existing is not None:
        return existing, False
    extension = DeadlineExtensionRequest.objects.create(
        match=match, requested_by=requested_by
    )
    transaction.on_commit(lambda: _notify_requested(extension.pk), robust=True)
    return extension, True


@transaction.atomic
def approve_deadline_extension(extension_id: int) -> bool:
    """Однократно добавить 24 часа; завершённый матч или старый участник не подходят."""
    from apps.telegram_bot.notifications import notify_extension_approved

    match_id = (
        DeadlineExtensionRequest.objects.filter(pk=extension_id)
        .values_list("match_id", flat=True)
        .first()
    )
    if match_id is None:
        return False
    match = lock_match(match_id)
    extension = (
        DeadlineExtensionRequest.objects.select_for_update()
        .filter(pk=extension_id)
        .first()
    )
    if (
        match is None
        or extension is None
        or extension.status != DeadlineExtensionRequest.Status.PENDING
    ):
        return False
    if match.status not in (Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS):
        return False
    if get_match_player_side(match, extension.requested_by) is None:
        return False
    now = timezone.now()
    match.deadline = (match.deadline or now) + timedelta(hours=24)
    match.deadline_overdue_notified_at = None
    match.save(update_fields=["deadline", "deadline_overdue_notified_at"])
    extension.match = match
    extension.status = DeadlineExtensionRequest.Status.APPROVED
    extension.processed_at = now
    extension.save(update_fields=["status", "processed_at"])
    notify_extension_approved(extension)
    return True
