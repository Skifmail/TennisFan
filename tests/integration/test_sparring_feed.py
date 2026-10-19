"""Общая лента спаррингов: свой город, расстояние и выбор формата."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse
from pytest_mock import MockerFixture

from apps.core.home_city_counters import build_home_city_counters
from apps.core.models import City
from apps.sparring.models import (
    DoublesMatchKind,
    DoublesMatchRequest,
    DoublesMatchRequestStatus,
    SparringRequest,
)
from apps.users.models import Player

pytestmark = pytest.mark.django_db
pytest_plugins = ("tests.support.sparring_feed_fixtures",)


def _single(player: Player, city: str, description: str = "Заявка") -> SparringRequest:
    return SparringRequest.objects.create(
        player=player, city=city, description=description
    )


def _double(player: Player, city: str, *, team: bool = False) -> DoublesMatchRequest:
    return DoublesMatchRequest.objects.create(
        created_by=player,
        city=city,
        kind=DoublesMatchKind.TEAM if team else DoublesMatchKind.CLASSIC,
    )


def test_feed_when_entering_should_mix_formats_and_rank_local_near_far_unknown(
    client: Client,
    viewer: Player,
) -> None:
    """Общая лента не требует переключения вкладок и не скрывает другие города."""
    unknown = _single(viewer, "Без координат", "Неизвестный город")
    far = _double(viewer, "Дальний город", team=True)
    near = _double(viewer, "Ближний город")
    local = _single(viewer, viewer.city, "Местная заявка")
    response = client.get(reverse("sparring_list"), secure=True)
    assert response.status_code == 200
    assert response.context["sparring_type"] == "all"
    entries = list(response.context["feed_page"])
    assert [(row.sparring_type, row.request.pk) for row in entries] == [
        ("singles", local.pk),
        ("doubles", near.pk),
        ("team", far.pk),
        ("singles", unknown.pk),
    ]
    html = response.content.decode()
    assert html.index("Рядом с вами") < html.index("Местная заявка")
    assert html.index("Местная заявка") < html.index("В других городах")
    assert html.index("В других городах") < html.index("Ближний город</span>")
    assert "type=all" in html
    assert "Неизвестный город" in html


@pytest.mark.parametrize("kind", ["singles", "doubles", "team"])
def test_feed_when_format_selected_should_only_show_selected_kind(
    client: Client,
    viewer: Player,
    kind: str,
) -> None:
    """Явный выбор формата ограничивает общую ленту одним типом."""
    _single(viewer, viewer.city)
    _double(viewer, viewer.city)
    _double(viewer, viewer.city, team=True)
    response = client.get(reverse("sparring_list"), {"type": kind}, secure=True)
    assert {row.sparring_type for row in response.context["feed_page"]} == {kind}
    assert response.context["feed_page"].paginator.count == 1


def test_feed_when_more_than_ten_should_paginate_combined_list_without_duplicates(
    client: Client,
    viewer: Player,
) -> None:
    """Размер страницы общий для всех форматов; сортировка сохраняется на границе."""
    for index in range(7):
        _single(viewer, viewer.city, f"Местная {index}")
    for _ in range(5):
        _double(viewer, "Ближний город")
    first = client.get(reverse("sparring_list"), secure=True)
    second = client.get(reverse("sparring_list"), {"page": 2}, secure=True)
    first_page, second_page = first.context["feed_page"], second.context["feed_page"]
    assert len(first_page) == 10
    assert len(second_page) == 2
    assert first_page.paginator.count == 12
    assert all(row.is_local for row in list(first_page)[:7])
    assert not any(row.is_local for row in second_page)
    assert {(row.sparring_type, row.request.pk) for row in first_page}.isdisjoint(
        {(row.sparring_type, row.request.pk) for row in second_page}
    )
    assert "page=2" in first.content.decode()
    assert "В других городах" in second.content.decode()


def test_feed_when_explicit_city_or_radius_should_preserve_filter(
    client: Client,
    viewer: Player,
) -> None:
    """Ручные фильтры не превращаются в простую подсказку сортировки."""
    _single(viewer, viewer.city)
    _single(viewer, "Ближний город")
    _double(viewer, "Дальний город")
    selected = client.get(
        reverse("sparring_list"), {"city": "Дальний город"}, secure=True
    )
    assert [row.request.city for row in selected.context["feed_page"]] == [
        "Дальний город"
    ]
    nearby = client.get(reverse("sparring_list"), {"nearby": 25}, secure=True)
    assert {row.request.city for row in nearby.context["feed_page"]} == {
        "Точка старта",
        "Ближний город",
    }


@pytest.mark.parametrize("page", ["abc", "-1", "9999"])
def test_feed_when_guest_or_bad_page_should_remain_available(
    client: Client,
    viewer: Player,
    page: str,
) -> None:
    """Гость без точки отсчёта видит заявки, ошибочная страница не даёт 500."""
    _single(viewer, viewer.city)
    _double(viewer, "Дальний город")
    client.logout()
    response = client.get(reverse("sparring_list"), {"page": page}, secure=True)
    assert response.status_code == 200
    assert len(response.context["feed_page"]) == 2
    assert "Рядом с вами" not in response.content.decode()


def test_feed_when_empty_or_profile_without_coordinates_should_not_hide_events(
    client: Client,
    viewer: Player,
) -> None:
    """Без координат свой город остаётся первым, остальные заявки доступны."""
    empty = client.get(reverse("sparring_list"), secure=True)
    assert empty.status_code == 200
    assert empty.context["feed_page"].paginator.count == 0
    Player.objects.filter(pk=viewer.pk).update(city="Без координат")
    other = _double(viewer, "Дальний город")
    local = _single(viewer, "Без координат")
    response = client.get(reverse("sparring_list"), secure=True)
    assert [row.request for row in response.context["feed_page"]] == [local, other]


def test_home_link_when_city_counter_clicked_should_not_force_city_or_type() -> None:
    """Счётчик на главной ведёт в общую ленту, сохраняя локальные числа."""
    counters = build_home_city_counters(
        city="Раменское",
        tournaments_seen_at=None,
        sparrings_seen_at=None,
    )
    query = parse_qs(urlsplit(counters.sparrings.url).query)
    assert "city" not in query
    assert "type" not in query


def test_feed_when_gender_level_sport_selected_should_apply_to_all_formats(
    client: Client,
    viewer: Player,
) -> None:
    """Фильтры уровня, пола и спорта не теряются при объединении моделей."""
    local = _single(viewer, viewer.city)
    SparringRequest.objects.filter(pk=local.pk).update(
        desired_category="experienced", preferred_gender="female", sport="padel"
    )
    pair = _double(viewer, viewer.city)
    DoublesMatchRequest.objects.filter(pk=pair.pk).update(
        desired_level="experienced", preferred_gender="female", sport="padel"
    )
    _double(viewer, viewer.city, team=True)
    response = client.get(
        reverse("sparring_list"),
        {
            "sport": "padel",
            "level": "experienced",
            "preferred_gender": "female",
        },
        secure=True,
    )
    assert {row.sparring_type for row in response.context["feed_page"]} == {
        "singles",
        "doubles",
    }
    assert response.context["feed_page"].paginator.count == 2


def test_feed_when_requests_closed_should_exclude_all_closed_formats(
    client: Client,
    viewer: Player,
) -> None:
    """Закрытые и отменённые заявки не появляются в общей ленте."""
    single = _single(viewer, viewer.city)
    SparringRequest.objects.filter(pk=single.pk).update(status="closed")
    for status in [
        DoublesMatchRequestStatus.CANCELLED,
        DoublesMatchRequestStatus.CONFIRMED,
    ]:
        pair = _double(viewer, viewer.city)
        DoublesMatchRequest.objects.filter(pk=pair.pk).update(status=status)
    response = client.get(reverse("sparring_list"), {"type": "invalid"}, secure=True)
    assert response.context["sparring_type"] == "all"
    assert response.context["feed_page"].paginator.count == 0


def test_feed_when_homonymous_village_exists_should_use_correct_city_coordinates(
    client: Client,
    viewer: Player,
    mocker: MockerFixture,
) -> None:
    """Одноимённая деревня не меняет порядок близких и дальних городов."""
    geocode = mocker.patch("apps.courts.geocoder.geocode_address")
    City.objects.filter(name="Раменское").delete()
    City.objects.create(
        name="Раменское", region="Московская область", lat=55.55, lng=38.27
    )
    City.objects.create(
        name="Раменское",
        region="Тверская область",
        settlement_type="derevnya",
        lat=56.5,
        lng=33.98,
    )
    Player.objects.filter(pk=viewer.pk).update(city="Раменское")
    near = _single(viewer, "Ближний город")
    far = _single(viewer, "Дальний город")
    response = client.get(reverse("sparring_list"), secure=True)
    entries = list(response.context["feed_page"])
    assert [row.request for row in entries] == [near, far]
    assert entries[0].distance_km < 25
    geocode.assert_not_called()


def test_feed_when_page_link_rendered_should_keep_explicit_filters(
    client: Client,
    viewer: Player,
) -> None:
    """Переход на следующую страницу не сбрасывает выбранные ограничения."""
    for _ in range(11):
        req = _single(viewer, viewer.city)
        SparringRequest.objects.filter(pk=req.pk).update(preferred_gender="male")
    response = client.get(
        reverse("sparring_list"),
        {
            "type": "singles",
            "city": viewer.city,
            "preferred_gender": "male",
            "nearby": 25,
        },
        secure=True,
    )
    from html.parser import HTMLParser

    class Links(HTMLParser):
        """Собирает ссылки пагинации из серверного HTML.

        Attributes:
            urls: Найденные адреса ссылок.
        """

        def __init__(self) -> None:
            """Инициализирует коллекцию ссылок."""
            super().__init__()
            self.urls: list[str] = []

        def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
            """Сохраняет адрес ссылки.

            Args:
                tag: Имя элемента.
                attrs: HTML-атрибуты.
            """
            if tag == "a":
                self.urls.extend(
                    value for key, value in attrs if key == "href" and value
                )

    links = Links()
    links.feed(response.content.decode())
    query = next(parse_qs(urlsplit(url).query) for url in links.urls if "page=2" in url)
    assert query["type"] == ["singles"]
    assert query["city"] == [viewer.city]
    assert query["preferred_gender"] == ["male"]
    assert query["nearby"] == ["25"]


def test_feed_when_switching_to_all_should_keep_strength_filter(
    client: Client,
    viewer: Player,
) -> None:
    """Выбор всех форматов сохраняет уровень, выбранный в одиночных."""
    response = client.get(
        reverse("sparring_list"),
        {
            "type": "singles",
            "category": "experienced",
        },
        secure=True,
    )
    assert response.context["selected_level"] == "experienced"
    assert 'name="level" value="experienced"' in response.content.decode()
