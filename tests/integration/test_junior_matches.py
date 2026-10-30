"""Сетки, свободные круги и исправление зависимых детских результатов."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.juniors.competition import register
from apps.juniors.matches import record_result, start_tournament
from apps.juniors.models import JuniorTournament
from tests.integration.test_junior_competition import participant, setup_tournament

pytestmark = pytest.mark.django_db


def ready_tournament(count=3, format_="elimination"):
    organizer, tournament, release = setup_tournament()
    tournament.format = format_
    tournament.capacity = 8
    tournament.save()
    for number in range(count):
        user, kid = participant(number, release)
        register(
            actor=user,
            child_id=kid.pk,
            tournament_id=tournament.pk,
            regulation_version=1,
        )
    JuniorTournament.objects.filter(pk=tournament.pk).update(
        registration_ends_at=timezone.now() - timedelta(minutes=1)
    )
    start_tournament(actor=organizer, tournament_id=tournament.pk)
    tournament.refresh_from_db()
    return organizer, tournament


def test_elimination_bye_and_correction_clear_dependent_result() -> None:
    organizer, tournament = ready_tournament()
    assert tournament.matches.count() == 3
    assert tournament.matches.filter(status="bye").count() == 1
    semifinal = tournament.matches.get(round_number=1, status="pending")
    record_result(actor=organizer, match_id=semifinal.pk, scores=[[4, 0]])
    final = tournament.matches.get(round_number=2)
    assert final.entry1_id and final.entry2_id
    record_result(actor=organizer, match_id=final.pk, scores=[[4, 1]])
    with pytest.raises(ValidationError):
        record_result(actor=organizer, match_id=semifinal.pk, scores=[[0, 4]])
    record_result(
        actor=organizer,
        match_id=semifinal.pk,
        scores=[[0, 4]],
        reason="Ошибка судьи при вводе",
    )
    final.refresh_from_db()
    assert final.status == "pending"
    assert final.winner_id is None
    assert final.scores == []


def test_round_robin_contains_each_pair_once() -> None:
    _, tournament = ready_tournament(4, "round_robin")
    assert tournament.matches.count() == 6
    pairs = {frozenset((m.entry1_id, m.entry2_id)) for m in tournament.matches.all()}
    assert len(pairs) == 6
