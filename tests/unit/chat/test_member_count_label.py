"""Проверки русских подписей численности городского сообщества."""

import pytest
from django.template.loader import render_to_string


@pytest.mark.parametrize(
    ("count", "label"),
    [
        (0, "0 участников"),
        (1, "1 участник"),
        (2, "2 участника"),
        (5, "5 участников"),
        (11, "11 участников"),
        (14, "14 участников"),
        (21, "21 участник"),
        (22, "22 участника"),
        (25, "25 участников"),
        (101, "101 участник"),
        (111, "111 участников"),
    ],
)
def test_member_count_when_rendered_should_use_russian_plural_form(
    count: int, label: str
) -> None:
    """Подпись использует корректную форму слова для нуля, единиц и десятков.

    Args:
        count: Численность сообщества.
        label: Ожидаемая подпись на карточке.
    """
    html = render_to_string("chat/_participant_count.html", {"count": count})
    assert label in html
    assert 'class="chat-participant-count"' in html
