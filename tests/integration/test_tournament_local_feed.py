"""Турниры своего города, остальные по расстоянию и переход с главной."""

from datetime import date, timedelta

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.home_city_counters import build_home_city_counters
from apps.core.models import City
from apps.tournaments.models import Tournament, TournamentStatus
from apps.users.models import Player
from tests.support.factories import make_player, make_tournament

pytestmark = pytest.mark.django_db


@pytest.fixture
def tournament_viewer(client: Client) -> Player:
    """Создаёт игрока с известными координатами города.

    Args:
        client: Клиент Django.

    Returns:
        Авторизованный игрок.
    """
    for name, lat, lng in [
        ("Старт", 55.5, 38.2),
        ("Ближе", 55.6, 38.3),
        ("Дальше", 43.5, 39.7),
    ]:
        City.objects.create(name=name, lat=lat, lng=lng)
    player = make_player(email_suffix="tournament-feed")
    Player.objects.filter(pk=player.pk).update(city="Старт")
    player.refresh_from_db()
    client.force_login(player.user)
    return player


def _event(slug: str, city: str, *, status: str = "active") -> Tournament:
    return make_tournament(name=slug, slug=slug, city=city, status=status)


def test_tournaments_when_entering_should_rank_own_near_far_unknown_and_group(
    client: Client,
    tournament_viewer: Player,
) -> None:
    """Близость определяется по городу игрока, а не по времени создания."""
    unknown = _event("unknown-event", "Без координат")
    far = _event("far-event", "Дальше")
    local = _event("local-event", "Старт")
    near = _event("near-event", "Ближе")
    response = client.get(reverse("tournament_list"), secure=True)
    assert response.context["tournaments"] == [local, near, far, unknown]
    html = response.content.decode()
    assert html.index("Рядом с вами") < html.index("local-event")
    assert (
        html.index("local-event")
        < html.index("В других городах")
        < html.index("near-event")
    )
    assert (
        response.context["tournaments"][1].distance_km
        < response.context["tournaments"][2].distance_km
    )


def test_tournaments_when_city_manually_selected_should_not_expand_scope(
    client: Client,
    tournament_viewer: Player,
) -> None:
    """Ручной фильтр продолжает ограничивать выдачу, остальные не добавляются."""
    _event("local-event", "Старт")
    selected = _event("far-event", "Дальше")
    response = client.get(reverse("tournament_list"), {"city": "Дальше"}, secure=True)
    assert response.context["tournaments"] == [selected]


def test_tournaments_when_second_page_crosses_city_boundary_should_keep_sections(
    client: Client,
    tournament_viewer: Player,
) -> None:
    """Группы корректны на границе существующей пагинации по 20 турниров."""
    for index in range(21):
        _event(f"local-{index}", "Старт")
    _event("near-event", "Ближе")
    response = client.get(reverse("tournament_list"), {"page": 2}, secure=True)
    assert len(response.context["tournaments"]) == 2
    assert [row.city for row in response.context["tournaments"]] == ["Старт", "Ближе"]
    assert "Рядом с вами" in response.content.decode()
    assert "В других городах" in response.content.decode()


def test_tournaments_when_guest_or_archive_should_preserve_existing_status_order(
    client: Client,
    tournament_viewer: Player,
) -> None:
    """Гости сохраняют сортировку статусов, архив — даты завершения."""
    _event("local-done", "Старт", status=str(TournamentStatus.COMPLETED))
    active = _event("far-active", "Дальше")
    client.logout()
    response = client.get(reverse("tournament_list"), secure=True)
    assert response.context["tournaments"][0] == active
    assert "Рядом с вами" not in response.content.decode()
    client.force_login(tournament_viewer.user)
    recent = _event("far-done", "Дальше", status=str(TournamentStatus.COMPLETED))
    Tournament.objects.filter(pk=recent.pk).update(
        end_date=date.today() + timedelta(days=1)
    )
    archive = client.get(reverse("tournament_archive"), secure=True)
    assert archive.context["tournaments"][0].pk == recent.pk
    assert "Рядом с вами" not in archive.content.decode()


def test_home_when_tournament_counter_clicked_should_open_unrestricted_feed() -> None:
    """Ссылка с главной не запирает игрока в одном городе."""
    counters = build_home_city_counters(
        city="Старт", tournaments_seen_at=None, sparrings_seen_at=None
    )
    assert counters.tournaments.url == reverse("tournament_list")
