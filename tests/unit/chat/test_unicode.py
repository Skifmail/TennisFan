"""Проверки текста, который невозможно безопасно сохранить в UTF-8."""

import pytest

from apps.chat.domain import ChatValidationError, normalize_message


@pytest.mark.parametrize("text", ["текст\ud800", "\udfff"])
def test_normalize_message_when_unpaired_surrogate_should_reject(text: str) -> None:
    """Невалидные Unicode-последовательности не приводят к HTTP 500."""
    with pytest.raises(ChatValidationError):
        normalize_message(text)
