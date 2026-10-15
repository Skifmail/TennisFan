"""Регрессия блокировки турнирной строки с необязательным клубом."""

import pytest
from pytest_mock import MockerFixture

from apps.tournaments.completion_notify import notify_tournament_completed
from apps.tournaments.models import TournamentStatus
from tests.support.factories import make_club, make_tournament


@pytest.mark.django_db
@pytest.mark.parametrize("has_club", [False, True])
def test_notify_tournament_completed_when_no_results_should_preserve_nullable_club_and_skip_claim(
    has_club: bool,
) -> None:
    """LEFT JOIN не блокируется; отсутствие результатов не помечается рассылкой."""
    club = make_club() if has_club else None
    tournament = make_tournament(status=TournamentStatus.COMPLETED, club=club)
    assert notify_tournament_completed(tournament.pk) == 0
    tournament.refresh_from_db()
    assert tournament.completion_notified_at is None


@pytest.mark.django_db(transaction=True)
def test_notify_tournament_completed_when_workers_race_should_claim_delivery_once(
    mocker: MockerFixture,
) -> None:
    """Два соединения PostgreSQL не получают право на одну рассылку одновременно."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connection

    from apps.core.db_context import background_connection
    from apps.tournaments.models import TournamentPlayerResult
    from tests.support.factories import make_player

    if connection.vendor != "postgresql":
        pytest.skip("Конкурентная блокировка проверяется на PostgreSQL")
    tournament = make_tournament(status=TournamentStatus.COMPLETED)
    player = make_player()
    TournamentPlayerResult.objects.create(tournament=tournament, player=player, place=1)
    delivery = mocker.patch(
        "apps.tournaments.completion_notify._deliver_completion_notifications",
        return_value=1,
    )
    start = Barrier(2)

    def notify() -> int:
        with background_connection():
            start.wait(timeout=10)
            return notify_tournament_completed(tournament.pk)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(notify) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(results) == [0, 1]
    delivery.assert_called_once()
