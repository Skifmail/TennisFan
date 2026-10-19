"""Компактное меню и комбинированный выбор форматов."""

from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse
from pytest_mock import MockerFixture

from apps.users.models import Player
from tests.integration.test_sparring_feed import _double, _single

pytestmark = pytest.mark.django_db
pytest_plugins = ("tests.support.sparring_feed_fixtures",)


def test_menu_when_multiple_formats_selected_should_filter_union_and_keep_links(
    client: Client,
    viewer: Player,
) -> None:
    """Показывает объединение выбранных форматов, не только последнюю галочку."""
    _single(viewer, viewer.city)
    _double(viewer, viewer.city)
    _double(viewer, viewer.city, team=True)
    response = client.get(
        reverse("sparring_list"), {"type": ["singles", "team"]}, secure=True
    )
    assert {row.sparring_type for row in response.context["feed_page"]} == {
        "singles",
        "team",
    }
    assert response.context["selected_formats"] == ("singles", "team")
    for option in response.context["sport_navigation"]:
        assert parse_qs(urlsplit(option["url"]).query)["type"] == ["singles", "team"]
    html = response.content.decode()
    assert 'name="type" value="singles"' in html
    assert 'name="type" value="doubles"' in html
    assert 'name="type" value="team"' in html
    assert 'class="sparring-compact__row"' in html
    assert 'class="sparring-compact__formats"' in html
    assert "Форматы · 2" in html


@pytest.mark.parametrize("access", [True, False])
def test_menu_when_access_changes_should_show_clear_primary_action_and_private_menu(
    client: Client,
    viewer: Player,
    mocker: MockerFixture,
    access: bool,
) -> None:
    """Личные ссылки спрятаны в «Мои», главное действие зависит от доступа."""
    mocker.patch("apps.sparring.views.user_has_sparring_access", return_value=access)
    response = client.get(reverse("sparring_list"), secure=True)
    html = response.content.decode()
    assert 'class="sparring-compact__mine"' in html
    assert "Мои заявки" in html and "Мои приглашения" in html
    assert 'class="sparring-compact__primary"' in html
    assert ("Создать заявку" if access else "Подписка") in html
    assert ("Пригласить на спарринг" in html) == access
    assert 'class="sparring-compact__filters"' in html
    assert 'class="sparring-compact__filters" open' not in html


def test_menu_when_explicit_filters_given_should_show_count_and_keep_multiple_types(
    client: Client,
    viewer: Player,
) -> None:
    """Свёрнутые фильтры заметны, а отправка не сбрасывает выбор форматов."""
    response = client.get(
        reverse("sparring_list"),
        {
            "type": ["singles", "team"],
            "city": viewer.city,
            "level": "experienced",
            "preferred_gender": "male",
            "nearby": 25,
        },
        secure=True,
    )
    assert response.context["active_filter_count"] == 4
    query = parse_qs(response.context["sparring_filter_query"])
    assert query["type"] == ["singles", "team"]
    assert query["preferred_gender"] == ["male"]
    assert 'aria-label="Активных фильтров: 4"' in response.content.decode()


def test_menu_when_multiple_formats_paginated_should_keep_union_on_second_page(
    client: Client,
    viewer: Player,
) -> None:
    """Пагинация не теряет повторяющиеся type и не добавляет невыбранные заявки."""
    for _ in range(11):
        _single(viewer, viewer.city)
    for _ in range(5):
        _double(viewer, viewer.city, team=True)
    _double(viewer, viewer.city)
    response = client.get(
        reverse("sparring_list"),
        {
            "type": ["singles", "team"],
            "page": ["2"],
        },
        secure=True,
    )
    page = response.context["feed_page"]
    assert page.paginator.count == 16
    assert len(page) == 6
    assert {row.sparring_type for row in page}.issubset({"singles", "team"})
    assert parse_qs(response.context["feed_pagination_query"])["type"] == [
        "singles",
        "team",
    ]
