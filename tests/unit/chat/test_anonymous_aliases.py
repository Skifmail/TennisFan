"""Проверки стабильных анонимных обозначений участников чата."""

from apps.chat.anonymous import ANONYMOUS_EMOJIS, anonymous_emoji_map


def test_anonymous_emoji_map_when_repeated_should_keep_assignments() -> None:
    """Повторный расчёт не меняет значки и не повторяет их внутри комнаты."""
    user_ids = list(range(1, 41))

    first = anonymous_emoji_map(room_id=17, user_ids=user_ids)
    second = anonymous_emoji_map(room_id=17, user_ids=user_ids)

    assert first == second
    assert set(first) == set(user_ids)
    assert len(set(first.values())) == len(user_ids)
    assert set(first.values()) <= set(ANONYMOUS_EMOJIS)
