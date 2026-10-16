"""Регрессии карты игроков при совпадении названий поселений."""

import pytest
from pytest_mock import MockerFixture

from apps.core.models import City
from apps.core.views import _build_cities_map_payload
from apps.users.models import Player
from tests.support.factories import make_player

pytestmark = pytest.mark.django_db


def test_map_when_ramenskoe_has_wrong_legacy_coords_should_use_moscow_region() -> None:
    """Карта исправляет старые координаты, не меняя профиль и рейтинг."""
    City.objects.filter(name="Раменское").delete()
    City.objects.create(name="Раменское", lat=56.5, lng=34.5)
    City.objects.create(
        name="Раменское",
        region="Тверская область",
        settlement_type="derevnya",
        lat=56.6,
        lng=34.6,
    )
    player = make_player(email_suffix="ramenskoe-map", points=5000)
    player.city = "Раменское"
    player.is_verified = True
    Player.objects.filter(pk=player.pk).update(city=player.city, is_verified=True)
    payload = _build_cities_map_payload()
    assert len(payload) == 1
    assert (payload[0]["lat"], payload[0]["lng"]) == (55.5495132, 38.2728914)
    assert payload[0]["players_count"] == 1
    player.refresh_from_db()
    assert player.city == "Раменское"
    assert player.total_points == 5000


def test_map_when_city_and_village_share_name_should_keep_separate_markers() -> None:
    """Тип и регион не теряются при группировке игроков."""
    City.objects.create(name="Одинаковый", region="Область А", lat=50, lng=40)
    City.objects.create(
        name="Одинаковый",
        region="Область Б",
        settlement_type="derevnya",
        lat=51,
        lng=41,
    )
    for index, city in enumerate(["Одинаковый", "деревня Одинаковый, Область Б"]):
        player = make_player(email_suffix=f"homonym-map-{index}")
        player.city = city
        player.is_verified = True
        Player.objects.filter(pk=player.pk).update(city=player.city, is_verified=True)
    payload = _build_cities_map_payload()
    assert len(payload) == 2
    assert {(row["lat"], row["lng"]) for row in payload} == {(50, 40), (51, 41)}


def test_map_when_city_is_ambiguous_should_not_geocode_random_region(
    mocker: MockerFixture,
) -> None:
    """Неоднозначный город не отправляется на повторное угадывание геокодеру."""
    geocode = mocker.patch("apps.courts.geocoder.geocode_address")
    for region in ["Область А", "Область Б"]:
        City.objects.create(name="Неоднозначный", region=region, lat=50, lng=40)
    player = make_player(email_suffix="ambiguous-map")
    player.city = "Неоднозначный"
    player.is_verified = True
    Player.objects.filter(pk=player.pk).update(city=player.city, is_verified=True)
    assert _build_cities_map_payload() == []
    geocode.assert_not_called()


def test_map_when_coordinates_missing_should_geocode_with_region(
    mocker: MockerFixture,
) -> None:
    """Регион сохраняется в запросе геокодеру для неизвестных координат."""
    mocker.patch("apps.core.views._get_geocoder_api_key", return_value="test-key")
    geocode = mocker.patch(
        "apps.courts.geocoder.geocode_address", return_value=(50.0, 40.0)
    )
    City.objects.create(name="Уникальный", region="Область А")
    player = make_player(email_suffix="region-map")
    player.city = "Уникальный, Область А"
    player.is_verified = True
    Player.objects.filter(pk=player.pk).update(city=player.city, is_verified=True)
    assert len(_build_cities_map_payload()) == 1
    assert geocode.call_args.args[0] == "Уникальный, Область А"
