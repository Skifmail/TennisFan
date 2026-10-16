"""Выделенное предупреждение о роли создателя городского сообщества."""

import pytest
from django.template.loader import render_to_string


@pytest.mark.parametrize("creating", [True, False])
def test_creation_notice_when_join_form_rendered_should_highlight_only_new_chat(
    creating: bool,
) -> None:
    """Показывает отдельный заголовок и значок только будущему администратору.

    Args:
        creating: Создаётся ли новый чат вместо вступления в существующий.
    """
    html = render_to_string("chat/_join_form.html", {"is_chat_creation": creating})
    if creating:
        assert 'class="chat-creation-notice"' in html
        assert "<strong>Вы станете администратором</strong>" in html
        assert 'src="/static/images/chat/administrator-badge.webp"' in html
        assert 'width="40" height="46" alt=""' in html
        assert "назначать модераторов и передавать управление" in html
    else:
        assert "chat-creation-notice" not in html
        assert "Вы станете администратором" not in html
