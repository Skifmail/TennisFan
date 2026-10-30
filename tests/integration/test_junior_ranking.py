"""Разделение сезонных рейтингов и безопасный перерасчёт после исправления."""

import pytest

from apps.juniors.matches import record_result
from apps.juniors.models import JuniorAward
from apps.juniors.ranking import category_key, finalize, leaderboard
from tests.integration.test_junior_matches import ready_tournament

pytestmark = pytest.mark.django_db


def test_rating_idempotency_and_correction_invalidation() -> None:
    organizer, tournament = ready_tournament(2, "round_robin")
    match = tournament.matches.get()
    record_result(actor=organizer, match_id=match.pk, scores=[[4, 0]])
    finalize(actor=organizer, tournament_id=tournament.pk)
    finalize(actor=organizer, tournament_id=tournament.pk)
    assert JuniorAward.objects.count() == 2
    rows = leaderboard(
        season=tournament.season, ball="red", category=category_key(tournament)
    )
    assert [row["points"] for row in rows] == [100, 70]
    assert rows[0]["label"].startswith("Участник ")
    assert (
        leaderboard(
            season=tournament.season, ball="green", category=category_key(tournament)
        )
        == []
    )
    record_result(
        actor=organizer, match_id=match.pk, scores=[[0, 4]], reason="Перепутаны стороны"
    )
    assert JuniorAward.objects.count() == 0
    finalize(actor=organizer, tournament_id=tournament.pk)
    assert JuniorAward.objects.get(child=match.entry2.child).points == 100
