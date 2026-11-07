"""Контракт общего помощника пагинации интерактивных списков."""

from __future__ import annotations

import pytest

from apps.telegram_bot.pagination import navigation_rows, normalize_offset


@pytest.mark.parametrize(
    ("offset", "total", "expected"),
    [
        (0, 7, 0),
        (-5, 7, 0),
        (5, 7, 5),
        (10, 7, 5),
        (100, 7, 5),
        (5, 0, 0),
        (5, -1, 0),
    ],
)
def test_normalize_offset_should_clamp_to_existing_page(
    offset: int, total: int, expected: int
) -> None:
    """Смещение из callback_data не должно приводить к пустой странице."""
    assert normalize_offset(offset, total) == expected


def test_normalize_offset_should_ignore_non_positive_page_size() -> None:
    """Нулевой или отрицательный размер страницы не делит на ноль."""
    assert normalize_offset(5, 7, page_size=0) == 0
    assert normalize_offset(5, 7, page_size=-3) == 0


def test_navigation_rows_should_hide_arrows_for_single_page() -> None:
    """Для одной страницы или пустого списка кнопки не нужны."""
    assert navigation_rows("page_", 0, 0) == []
    assert navigation_rows("page_", 0, 5) == []


def test_navigation_rows_should_only_offer_next_on_first_page() -> None:
    """На первой странице доступна только кнопка «Далее»."""
    assert navigation_rows("page_", 0, 12) == [
        [{"text": "➡️ Далее", "callback_data": "page_5"}]
    ]


def test_navigation_rows_should_offer_both_directions_in_the_middle() -> None:
    """В середине списка доступны обе кнопки с соседними смещениями."""
    assert navigation_rows("page_", 5, 12) == [
        [{"text": "⬅️ Назад", "callback_data": "page_0"}],
        [{"text": "➡️ Далее", "callback_data": "page_10"}],
    ]


def test_navigation_rows_should_only_offer_back_on_last_page() -> None:
    """На последней странице «Далее» не показывается."""
    assert navigation_rows("page_", 10, 12) == [
        [{"text": "⬅️ Назад", "callback_data": "page_5"}]
    ]
