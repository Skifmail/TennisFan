"""Сохраняемая, идемпотентная подготовка уведомлений о новых турнирах."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Final

from django.db import transaction
from django.db.models import Max, QuerySet
from django.urls import reverse
from django.utils import timezone
from loguru import logger

from apps.core.localities import locality_tokens
from apps.notifications.domain.rules import BODY_LIMIT, TITLE_LIMIT
from apps.notifications.models import Delivery
from apps.notifications.services import create_notification
from apps.tournaments.models import Tournament
from apps.users.models import User

from .delivery import enqueue_message
from .models import TournamentAnnouncement

BATCH_SIZE: Final = 100
LIFETIME: Final = timedelta(days=1)


def request_announcement(tournament_pk: int) -> TournamentAnnouncement:
    """Зафиксировать подготовку в транзакции создания турнира, без фонового потока."""
    announcement: TournamentAnnouncement
    announcement, _ = TournamentAnnouncement.objects.get_or_create(
        tournament_id=tournament_pk
    )
    return announcement


def _candidate_users(tournament: Tournament) -> QuerySet[User]:
    """Выбрать кандидатов без полного сканирования городов в Python."""
    users = User.objects.filter(is_active=True)
    if tournament.club_id:
        from apps.clubs.models import ClubMember, ClubMemberStatus

        members = ClubMember.objects.filter(
            club_id=tournament.club_id, status=ClubMemberStatus.ACTIVE
        ).values("user_id")
        return users.filter(pk__in=members)
    return users.filter(player__is_bye=False).exclude(player__city="")


def _candidate_allowed(tournament: Tournament, user: User) -> bool:
    """Сопоставить один населённый пункт, сохраняя нормализацию названий."""
    if tournament.status in {"completed", "cancelled"}:
        return False
    if tournament.club_id:
        return True
    return bool(
        set(locality_tokens(tournament.city)).intersection(
            locality_tokens(user.player.city)
        )
    )


def tournament_recipient_allowed(tournament: Tournament, user_id: int) -> bool:
    """Перепроверить актуальную аудиторию перед доставкой, не сканируя всех игроков."""
    user = (
        _candidate_users(tournament).select_related("player").filter(pk=user_id).first()
    )
    return user is not None and _candidate_allowed(tournament, user)


def telegram_recipient_allowed(tournament: Tournament, user_id: int) -> bool:
    """Проверить аудиторию и независимые клубные согласия Telegram без записи в БД."""
    if not tournament_recipient_allowed(tournament, user_id):
        return False
    return _telegram_consent_allowed(tournament, user_id)


def _telegram_consent_allowed(tournament: Tournament, user_id: int) -> bool:
    """Прочитать независимое согласие клубного Telegram без почтового fallback."""
    if not tournament.club_id:
        return True
    from apps.clubs.models import ClubNotificationConfig, ClubNotificationSettings

    if not ClubNotificationConfig.objects.filter(
        club_id=tournament.club_id,
        notify_by_telegram=True,
        tournament_reminders_enabled=True,
    ).exists():
        return False
    preferences = ClubNotificationSettings.objects.filter(
        club_id=tournament.club_id, user_id=user_id
    ).first()
    return preferences is None or (
        preferences.is_enabled and preferences.telegram_enabled
    )


@transaction.atomic
def _announcement_keyboard(tournament: Tournament, detail_path: str) -> dict[str, Any]:
    """Собрать кнопки анонса: карточка турнира и, если можно, регистрация."""
    from apps.tournaments.models import TournamentStatus

    from .notifications import _get_site_base_url

    base = _get_site_base_url().rstrip("/")
    buttons: list[list[dict[str, str]]] = []
    registration_open = (
        tournament.status
        not in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED)
        and not getattr(tournament, "bracket_generated", False)
        and not getattr(tournament, "postpayment_window_started_at", None)
        and not tournament.is_full()
    )
    if registration_open:
        buttons.append(
            [
                {
                    "text": "✍️ Зарегистрироваться",
                    "url": base
                    + reverse("tournament_register", args=[tournament.slug]),
                }
            ]
        )
    buttons.append([{"text": "🔗 Подробнее о турнире", "url": base + detail_path}])
    return {"inline_keyboard": buttons}


@transaction.atomic
def prepare_announcement(announcement_pk: int, *, batch_size: int = BATCH_SIZE) -> int:
    """Подготовить один пакет; ЛК, задания и курсор коммитятся вместе."""
    from .notifications import _format_new_tournament_message

    if batch_size <= 0:
        return 0
    job = TournamentAnnouncement.objects.select_for_update().get(pk=announcement_pk)
    if job.status != "queued":
        return 0
    tournament = job.tournament
    now = timezone.now()
    if job.created_at + LIFETIME <= now or tournament.status in {
        "completed",
        "cancelled",
    }:
        job.status = "expired"
        job.prepared_at = now
        job.save(update_fields=["status", "prepared_at"])
        return 0
    if job.started_at is None:
        job.started_at = now
        job.upper_user_id = User.objects.aggregate(value=Max("pk"))["value"] or 0
    candidates = (
        _candidate_users(tournament)
        .filter(pk__gt=job.cursor_user_id, pk__lte=job.upper_user_id)
        .order_by("pk")
    )
    users = list(candidates.select_related("player")[:batch_size])
    url = reverse("tournament_detail", args=[tournament.slug])
    message = f"Новый турнир: «{tournament.name}». Приглашаем принять участие!"[:255]
    telegram_text = _format_new_tournament_message(tournament)
    reply_markup = _announcement_keyboard(tournament, url)
    for user in users:
        job.cursor_user_id = user.pk
        if not _candidate_allowed(tournament, user):
            continue
        note = create_notification(
            user=user, message=message, url=url, event_key="tournament.created"
        )
        if user.email:
            Delivery.objects.get_or_create(
                tournament_announcement=job,
                user=user,
                channel="email",
                defaults={
                    "event_key": "tournament.created",
                    "notification": note,
                    "title": f"Новый турнир: {tournament.name}"[:TITLE_LIMIT],
                    "body": message[:BODY_LIMIT],
                    "url": url,
                    "available_at": now,
                    "expires_at": job.created_at + LIFETIME,
                },
            )
        if _telegram_consent_allowed(tournament, user.pk):
            enqueue_message(
                user,
                telegram_text,
                reply_markup=reply_markup,
                event_key="tournament.created",
                announcement=job,
                expires_at=job.created_at + LIFETIME,
            )
    if not candidates.filter(pk__gt=job.cursor_user_id).exists():
        job.status = "prepared"
        job.prepared_at = now
    job.save(
        update_fields=[
            "started_at",
            "upper_user_id",
            "cursor_user_id",
            "status",
            "prepared_at",
        ]
    )
    return len(users)


def process_announcements(*, limit: int = BATCH_SIZE) -> int:
    """Просмотреть не больше limit кандидатов за запуск cron."""
    handled = 0
    for job_pk in (
        TournamentAnnouncement.objects.filter(status="queued")
        .order_by("pk")
        .values_list("pk", flat=True)[: max(0, limit)]
    ):
        try:
            handled += prepare_announcement(job_pk, batch_size=limit - handled)
        except Exception as exc:
            logger.error(
                "Ошибка подготовки турнира: announcement={}, error={}",
                job_pk,
                type(exc).__name__,
            )
        if handled >= limit:
            break
    return handled
