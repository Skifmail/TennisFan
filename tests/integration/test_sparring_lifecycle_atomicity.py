"""Обработчики спаррингов используют свежие данные и единый порядок блокировок."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.sparring.models import SparringRequest, SparringResponse
from apps.sparring.services import expire_due_sparring_responses
from apps.tournaments.models import Match
from tests.support.factories import make_player

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("concurrent_change", ["completed", "extended"])
def test_overdue_command_preserves_changes_saved_after_selection(
    concurrent_change: str,
) -> None:
    """Сохранённый результат или продление нельзя перезаписать старым снимком cron."""
    author = make_player(email_suffix="overdue-race-author")
    respondent = make_player(email_suffix="overdue-race-respondent")
    match = Match.objects.create(
        match_type=Match.MatchType.SPARRING,
        player1=author,
        player2=respondent,
        status=Match.MatchStatus.SCHEDULED,
        rating_status=Match.RatingCalcStatus.PENDING,
        deadline=timezone.now() - timedelta(days=4),
    )
    fresh_deadline = timezone.now() + timedelta(days=7)
    original_atomic = transaction.atomic
    changed = False

    @contextmanager
    def save_change_before_transaction(*args: Any, **kwargs: Any) -> Iterator[None]:
        """Воспроизвести коммит другого запроса перед транзакцией обработки матча."""
        nonlocal changed
        if not changed:
            changed = True
            if concurrent_change == "completed":
                Match.objects.filter(pk=match.pk).update(
                    status=Match.MatchStatus.COMPLETED,
                    rating_status=Match.RatingCalcStatus.CALCULATED,
                    winner=author,
                )
            else:
                Match.objects.filter(pk=match.pk).update(deadline=fresh_deadline)
        with original_atomic(*args, **kwargs):
            yield

    with patch(
        "apps.sparring.management.commands.sparring_process_overdue_matches.transaction.atomic",
        side_effect=save_change_before_transaction,
    ):
        call_command("sparring_process_overdue_matches")

    match.refresh_from_db()
    assert changed
    if concurrent_change == "completed":
        assert match.status == Match.MatchStatus.COMPLETED
        assert match.rating_status == Match.RatingCalcStatus.CALCULATED
        assert match.winner_id == author.pk
    else:
        assert match.status == Match.MatchStatus.SCHEDULED
        assert match.deadline == fresh_deadline
        assert match.rating_status == Match.RatingCalcStatus.PENDING


def test_expiry_locks_request_before_its_responses() -> None:
    """Истечение не берёт блокировки в порядке, обратном принятию и отклонению."""
    author = make_player(email_suffix="expiry-lock-author")
    respondent = make_player(email_suffix="expiry-lock-respondent")
    request = SparringRequest.objects.create(player=author, city="Москва")
    response = SparringResponse.objects.create(
        sparring_request=request,
        respondent=respondent,
        expires_at=timezone.now() - timedelta(hours=1),
    )
    original_select_for_update = QuerySet.select_for_update
    locks: list[str] = []

    def record_lock(
        queryset: QuerySet[Any], *args: Any, **kwargs: Any
    ) -> QuerySet[Any]:
        """Записать порядок блокируемых моделей, сохранив настоящий QuerySet."""
        locks.append(queryset.model._meta.label)
        return original_select_for_update(queryset, *args, **kwargs)

    with patch.object(QuerySet, "select_for_update", new=record_lock):
        assert expire_due_sparring_responses() == 1

    relevant_locks = [
        model
        for model in locks
        if model in {"sparring.SparringRequest", "sparring.SparringResponse"}
    ]
    assert relevant_locks == ["sparring.SparringRequest", "sparring.SparringResponse"]
    response.refresh_from_db()
    assert response.status == SparringResponse.ResponseStatus.EXPIRED
