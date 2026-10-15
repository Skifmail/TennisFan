"""Проверки чистых правил городского чата."""

import pytest

from apps.chat.domain import ChatValidationError, normalize_message


@pytest.mark.parametrize("text", ["", " \n\t", "x" * 2001, "текст\x00"])
def test_normalize_message_when_invalid_should_raise(text: str) -> None:
    """Недопустимый текст отклоняется до обращения к хранилищу."""
    with pytest.raises(ChatValidationError):
        normalize_message(text)


def test_normalize_message_when_boundary_should_preserve_text() -> None:
    """Максимальная длина разрешена, HTML остаётся обычным текстом."""
    assert normalize_message("x" * 2000) == "x" * 2000
    assert (
        normalize_message("  <script>привет</script> \n") == "<script>привет</script>"
    )
