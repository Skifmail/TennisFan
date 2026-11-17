"""Жизненный цикл отклика: дедлайн 48 ч, авто-принятие, истечение и отклонение."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.sparring.models import (
    SparringMatchReminder,
    SparringReminderStage,
    SparringRequest,
    SparringResponse,
)
from apps.sparring.services import (
    SPARRING_CONFIRMATION_HOURS,
    auto_accept_sparring_response,
    expire_due_sparring_responses,
    reject_sparring_response,
    respond_to_sparring,
)
from apps.tournaments.models import Match
from apps.users.models import Notification, Player
from tests.support.factories import make_player, make_subscription

pytestmark = pytest.mark.django_db


def _player(suffix: str) -> Player:
    """Создать подтверждённого игрока с доступом к спаррингам."""
    player = make_player(
        email_suffix=f"lifecycle-{suffix}",
        phone=f"+7999000{suffix[:4]:0>4}",
        email_verified=True,
    )
    Player.objects.filter(pk=player.pk).update(is_verified=True)
    make_subscription(
        player.user,
        tier_name=f"lifecycle-{suffix}",
        tier_kwargs={"has_sparring": True},
    )
    player.refresh_from_db()
    return player


def _request(author: Player) -> SparringRequest:
    """Создать активную заявку автора."""
    return SparringRequest.objects.create(
        player=author,
        city="Москва",
        description="Ищу партнёра на спарринг.",
    )


def _response(
    request: SparringRequest,
    respondent: Player,
    *,
    status: str = SparringResponse.ResponseStatus.PENDING,
    expires_in: timedelta | None = None,
) -> SparringResponse:
    """Создать отклик с заданным сроком ожидания."""
    expires_at = timezone.now() + expires_in if expires_in is not None else None
    return SparringResponse.objects.create(
        sparring_request=request,
        respondent=respondent,
        contact_method="telegram",
        status=status,
        expires_at=expires_at,
    )


def _set_confirmation_deadline(
    request: SparringRequest, deadline: datetime | None
) -> None:
    """Сохранить дедлайн подтверждения без пересчёта по откликам."""
    SparringRequest.objects.filter(pk=request.pk).update(confirmation_deadline=deadline)
    request.refresh_from_db()


def _respond(request: SparringRequest, respondent: Player) -> SparringResponse:
    """Отправить отклик через сервис, не обращаясь к внешнему боту."""
    with patch("apps.telegram_bot.notifications.bot.is_configured", return_value=False):
        response, _ = respond_to_sparring(
            request.pk, respondent, contact_method="telegram"
        )
    return response


def test_response_sets_48_hour_deadline_for_author() -> None:
    """Отклик получает срок 48 часов, и заявка показывает тот же дедлайн."""
    author = _player("1001")
    respondent = _player("1002")
    request = _request(author)

    response = _respond(request, respondent)
    response.refresh_from_db()
    request.refresh_from_db()

    assert response.expires_at is not None
    delta = response.expires_at - timezone.now()
    assert (
        timedelta(hours=SPARRING_CONFIRMATION_HOURS - 1)
        < delta
        <= timedelta(hours=SPARRING_CONFIRMATION_HOURS)
    )
    assert request.confirmation_deadline == response.expires_at
    assert request.confirmation_hours_left == SPARRING_CONFIRMATION_HOURS


def test_pending_retry_does_not_postpone_author_deadline() -> None:
    """Повторный POST уже ожидающего отклика не начинает новые 48 часов."""
    author = _player("1101")
    respondent = _player("1102")
    request = _request(author)
    response = _respond(request, respondent)
    expires_at = response.expires_at
    assert expires_at is not None

    with patch(
        "apps.sparring.services.timezone.now",
        return_value=expires_at - timedelta(hours=1),
    ):
        repeated = _respond(request, respondent)

    repeated.refresh_from_db()
    request.refresh_from_db()
    assert repeated.pk == response.pk
    assert repeated.expires_at == expires_at
    assert request.confirmation_deadline == expires_at


def test_retried_rejection_does_not_jump_ahead_of_earlier_pending_response() -> None:
    """Заново отправленный отклик не обгоняет уже ожидающего решения игрока."""
    author = _player("1103")
    retried_player = _player("1104")
    waiting_player = _player("1105")
    request = _request(author)
    _response(request, retried_player, status=SparringResponse.ResponseStatus.REJECTED)
    waiting = _response(request, waiting_player, expires_in=timedelta(hours=-1))
    _respond(request, retried_player)

    match = auto_accept_sparring_response(request.pk)

    assert match is not None
    assert match.sparring_response_id == waiting.pk


def test_auto_accept_creates_match_and_closes_request() -> None:
    """После дедлайна первый ожидающий отклик принимается автоматически."""
    author = _player("1003")
    first = _player("1004")
    second = _player("1005")
    request = _request(author)
    accepted = _response(request, first, expires_in=timedelta(hours=-1))
    _response(request, second, expires_in=timedelta(hours=-1))
    _set_confirmation_deadline(request, timezone.now() - timedelta(minutes=1))

    match = auto_accept_sparring_response(request.pk)

    assert match is not None
    assert match.sparring_response_id == accepted.pk
    accepted.refresh_from_db()
    request.refresh_from_db()
    assert accepted.status == SparringResponse.ResponseStatus.ACCEPTED
    assert accepted.accepted_at is not None
    assert accepted.expires_at is None
    assert request.status == SparringRequest.Status.CLOSED
    assert request.match_id == match.pk
    assert request.matched_at is not None
    assert request.confirmation_deadline is None
    assert (
        request.responses.filter(
            status=SparringResponse.ResponseStatus.REJECTED
        ).count()
        == 1
    )
    assert Notification.objects.filter(user=author.user).exists()
    assert Notification.objects.filter(user=first.user).exists()


def test_auto_accept_waits_until_deadline() -> None:
    """До истечения 48 часов авто-принятие не создаёт матч."""
    author = _player("1006")
    respondent = _player("1007")
    request = _request(author)
    _response(request, respondent, expires_in=timedelta(hours=10))
    _set_confirmation_deadline(request, timezone.now() + timedelta(hours=10))

    assert auto_accept_sparring_response(request.pk) is None
    request.refresh_from_db()
    assert request.status == SparringRequest.Status.ACTIVE
    assert not Match.objects.exists()


def test_reject_response_keeps_request_open_and_moves_deadline() -> None:
    """Отклонение одного отклика оставляет заявку открытой ради второго."""
    author = _player("1008")
    first = _player("1009")
    second = _player("1010")
    request = _request(author)
    rejected = _response(request, first, expires_in=timedelta(hours=5))
    later = _response(request, second, expires_in=timedelta(hours=20))
    _set_confirmation_deadline(request, later.expires_at)

    reject_sparring_response(rejected.pk, author.user_id)

    rejected.refresh_from_db()
    request.refresh_from_db()
    assert rejected.status == SparringResponse.ResponseStatus.REJECTED
    assert rejected.expires_at is None
    assert request.status == SparringRequest.Status.ACTIVE
    assert request.confirmation_deadline == later.expires_at
    assert Notification.objects.filter(user=first.user).exists()


def test_expired_responses_are_closed_and_deadline_cleared() -> None:
    """Просроченный отклик становится expired и больше не держит заявку."""
    author = _player("1011")
    respondent = _player("1012")
    request = _request(author)
    response = _response(request, respondent, expires_in=timedelta(hours=-1))
    _set_confirmation_deadline(request, timezone.now() - timedelta(minutes=5))

    expired = expire_due_sparring_responses()

    response.refresh_from_db()
    request.refresh_from_db()
    assert expired == 1
    assert response.status == SparringResponse.ResponseStatus.EXPIRED
    assert request.confirmation_deadline is None
    assert Notification.objects.filter(user=respondent.user).exists()


def test_process_command_accepts_before_expiring() -> None:
    """Cron сначала создаёт матч, а не помечает тот же отклик истёкшим."""
    author = _player("1013")
    first = _player("1014")
    second = _player("1015")
    request = _request(author)
    accepted = _response(request, first, expires_in=timedelta(hours=-1))
    _response(request, second, expires_in=timedelta(hours=-1))
    _set_confirmation_deadline(request, timezone.now() - timedelta(minutes=1))

    call_command("process_sparring_responses")

    accepted.refresh_from_db()
    request.refresh_from_db()
    assert accepted.status == SparringResponse.ResponseStatus.ACCEPTED
    assert request.match_id is not None
    assert not SparringResponse.objects.filter(
        status=SparringResponse.ResponseStatus.EXPIRED
    ).exists()


def test_sparring_reminder_is_sent_once_per_stage() -> None:
    """Повторный запуск напоминаний не создаёт дубль этапа."""
    author = _player("1016")
    respondent = _player("1017")
    match = Match.objects.create(
        match_type=Match.MatchType.SPARRING,
        player1=author,
        player2=respondent,
        status=Match.MatchStatus.SCHEDULED,
        deadline=timezone.now() + timedelta(days=3),
    )

    with patch(
        "apps.sparring.management.commands.send_sparring_result_reminders._notify_match_participants"
    ) as notify:
        call_command("send_sparring_result_reminders")
        call_command("send_sparring_result_reminders")

    notify.assert_called_once()
    assert (
        SparringMatchReminder.objects.filter(
            match=match, stage=SparringReminderStage.D3
        ).count()
        == 1
    )


def test_overdue_command_cancels_only_matches_past_grace_period() -> None:
    """Через 3 дня после дедлайна спарринг отменяется без изменения рейтинга."""
    author = _player("1018")
    respondent = _player("1019")
    overdue = Match.objects.create(
        match_type=Match.MatchType.SPARRING,
        player1=author,
        player2=respondent,
        status=Match.MatchStatus.SCHEDULED,
        deadline=timezone.now() - timedelta(days=4),
        rating_status=Match.RatingCalcStatus.PENDING,
    )
    recent = Match.objects.create(
        match_type=Match.MatchType.SPARRING,
        player1=author,
        player2=respondent,
        status=Match.MatchStatus.SCHEDULED,
        deadline=timezone.now() - timedelta(days=2),
        rating_status=Match.RatingCalcStatus.PENDING,
    )

    call_command("sparring_process_overdue_matches")

    overdue.refresh_from_db()
    recent.refresh_from_db()
    assert overdue.status == Match.MatchStatus.CANCELLED
    assert overdue.rating_status == Match.RatingCalcStatus.NOT_APPLICABLE
    assert recent.status == Match.MatchStatus.SCHEDULED
    assert recent.rating_status == Match.RatingCalcStatus.PENDING
    assert Notification.objects.filter(user=author.user).exists()


def test_stale_request_without_auto_accept_is_closed() -> None:
    """Старая заявка без матча закрывается, а ожидающие отклики отклоняются."""
    author = _player("1020")
    respondent = _player("1021")
    request = _request(author)
    response = _response(request, respondent)
    SparringRequest.objects.filter(pk=request.pk).update(
        created_at=timezone.now() - timedelta(days=30)
    )

    call_command("close_stale_sparring_requests")

    request.refresh_from_db()
    response.refresh_from_db()
    assert request.status == SparringRequest.Status.CLOSED
    assert response.status == SparringResponse.ResponseStatus.REJECTED


def test_stale_request_with_auto_accept_creates_match() -> None:
    """Backfill принимает ожидающий отклик старой заявки и создаёт матч."""
    author = _player("1022")
    respondent = _player("1023")
    request = _request(author)
    response = _response(request, respondent)
    SparringRequest.objects.filter(pk=request.pk).update(
        created_at=timezone.now() - timedelta(days=30)
    )

    call_command("close_stale_sparring_requests", "--auto-accept")

    request.refresh_from_db()
    response.refresh_from_db()
    assert request.match_id is not None
    assert request.status == SparringRequest.Status.CLOSED
    assert response.status == SparringResponse.ResponseStatus.ACCEPTED


def test_reject_view_rejects_response_for_author(client: Client) -> None:
    """POST автора отклоняет отклик и возвращает его в «Мои заявки»."""
    author = _player("1024")
    respondent = _player("1025")
    request = _request(author)
    response = _response(request, respondent)
    client.force_login(author.user)

    result = client.post(
        reverse("sparring_reject_response", args=[response.pk]), secure=True
    )

    response.refresh_from_db()
    assert result.status_code == 302
    assert response.status == SparringResponse.ResponseStatus.REJECTED
    assert request.status == SparringRequest.Status.ACTIVE


@pytest.mark.parametrize(
    "command",
    [
        "process_sparring_responses",
        "send_sparring_result_reminders",
        "sparring_process_overdue_matches",
        "close_stale_sparring_requests",
    ],
)
def test_dry_run_does_not_change_requests_responses_or_matches(command: str) -> None:
    """Предварительный запуск команд не меняет данные и не отправляет уведомления."""
    author = _player("1106")
    respondent = _player("1107")
    request = _request(author)
    response = _response(request, respondent, expires_in=timedelta(hours=-1))
    _set_confirmation_deadline(request, response.expires_at)
    SparringRequest.objects.filter(pk=request.pk).update(
        created_at=timezone.now() - timedelta(days=30)
    )
    match = Match.objects.create(
        match_type=Match.MatchType.SPARRING,
        player1=author,
        player2=respondent,
        deadline=timezone.now() - timedelta(days=4),
    )
    before_requests = list(SparringRequest.objects.values())
    before_responses = list(SparringResponse.objects.values())
    before_matches = list(Match.objects.values())
    before_notifications = Notification.objects.count()

    call_command(command, "--dry-run")

    assert list(SparringRequest.objects.values()) == before_requests
    assert list(SparringResponse.objects.values()) == before_responses
    assert list(Match.objects.values()) == before_matches
    assert Notification.objects.count() == before_notifications
    assert not SparringMatchReminder.objects.filter(match=match).exists()
