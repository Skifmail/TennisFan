"""Публичные таблицы показывают медали только по итогам турнира."""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.tournaments.models import TournamentPlayerResult, TournamentStatus
from tests.support.factories import make_player, make_tournament


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["tournament_detail", "tournament_tables_detail"])
def test_fan_podium_uses_confirmed_final_results(client: Client, url_name: str) -> None:
    """До финала места пусты, а после него золото получает победитель матча."""
    tournament = make_tournament(
        start_date=timezone.localdate() + timedelta(days=7),
        status=TournamentStatus.UPCOMING,
    )
    finalist = make_player(email_suffix="finalist", points=2000)
    tournament.participants.add(finalist)
    url = reverse(url_name, args=[tournament.slug])
    if url_name == "tournament_tables_detail":
        client.force_login(finalist.user)

    response = client.get(url, secure=True)
    assert response.status_code == 200
    assert "🥇" not in response.content.decode()
    assert response.context["standings"][0]["place"] is None
    if url_name == "tournament_detail":
        assert "Турнирная таблица" not in response.content.decode()

    winner = make_player(email_suffix="winner", points=1000)
    semifinalist = make_player(email_suffix="semifinalist", points=3000)
    tournament.participants.add(winner, semifinalist)
    tournament.status = TournamentStatus.ACTIVE
    tournament.save(update_fields=["status"])
    TournamentPlayerResult.objects.create(
        tournament=tournament,
        player=finalist,
        round_eliminated=TournamentPlayerResult.RoundEliminated.FINAL,
        fan_points=50,
    )
    TournamentPlayerResult.objects.create(
        tournament=tournament,
        player=semifinalist,
        round_eliminated=TournamentPlayerResult.RoundEliminated.SF,
        fan_points=25,
    )
    response = client.get(url, secure=True)
    assert response.status_code == 200
    assert all(row["place"] is None for row in response.context["standings"])

    tournament.status = TournamentStatus.COMPLETED
    tournament.save(update_fields=["status"])
    response = client.get(url, secure=True)
    assert response.status_code == 200
    assert "🥇" not in response.content.decode()

    TournamentPlayerResult.objects.create(
        tournament=tournament,
        player=winner,
        round_eliminated=TournamentPlayerResult.RoundEliminated.WINNER,
        fan_points=50,
    )
    response = client.get(url, secure=True)
    assert response.status_code == 200
    assert [(row["player"], row["place"]) for row in response.context["standings"]] == [
        (winner, 1),
        (finalist, 2),
        (semifinalist, None),
    ]
    assert "🥇" in response.content.decode()
    assert "🥈" in response.content.decode()
    assert "🥉" not in response.content.decode()
