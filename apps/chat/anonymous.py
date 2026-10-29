"""Стабильные анонимные обозначения участников городского чата."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Final

ANONYMOUS_EMOJIS: Final[tuple[str, ...]] = (
    "🦊",
    "🐻",
    "🐼",
    "🐨",
    "🐯",
    "🦁",
    "🐮",
    "🐷",
    "🐸",
    "🐵",
    "🐔",
    "🐧",
    "🐦",
    "🦆",
    "🦅",
    "🦉",
    "🦇",
    "🐺",
    "🐗",
    "🐴",
    "🦄",
    "🐝",
    "🦋",
    "🐌",
    "🐞",
    "🐜",
    "🕷️",
    "🦂",
    "🐢",
    "🐍",
    "🦎",
    "🦖",
    "🦕",
    "🐙",
    "🦑",
    "🦐",
    "🦞",
    "🦀",
    "🐡",
    "🐠",
    "🐟",
    "🐬",
    "🐳",
    "🐋",
    "🦈",
    "🦭",
    "🐊",
    "🐅",
    "🐆",
    "🦓",
    "🦍",
    "🦧",
    "🦣",
    "🐘",
    "🦛",
    "🦏",
    "🐪",
    "🐫",
    "🦒",
    "🦘",
    "🦬",
    "🐃",
    "🐂",
    "🐄",
    "🐎",
    "🐖",
    "🐏",
    "🐑",
    "🦙",
    "🐐",
    "🦌",
    "🐕",
    "🐩",
    "🦮",
    "🐈",
    "🪶",
    "🐓",
    "🦃",
    "🦤",
    "🦚",
    "🦜",
    "🦢",
    "🦩",
    "🕊️",
    "🐇",
    "🦝",
    "🦨",
    "🦡",
    "🦫",
    "🦦",
    "🦥",
    "🐁",
    "🐀",
    "🐿️",
    "🦔",
    "🐉",
    "🐲",
    "🌵",
    "🎄",
    "🌲",
    "🌳",
    "🌴",
    "🌱",
    "🌿",
    "☘️",
    "🍀",
    "🎍",
    "🪴",
    "🍃",
    "🍂",
    "🍁",
    "🍄",
    "🐚",
    "🪨",
    "🌾",
    "💐",
    "🌷",
    "🌹",
    "🥀",
    "🌺",
    "🌻",
    "🌼",
    "🌸",
    "🌞",
    "🌝",
    "🌚",
    "🌙",
    "⭐",
    "🌟",
    "✨",
    "⚡",
    "🔥",
    "🌈",
    "☀️",
    "⛅",
    "☁️",
    "🌊",
    "💧",
    "💦",
    "☔",
)
FALLBACK_EMOJI: Final = "🙂"


def anonymous_emoji_map(*, room_id: int, user_ids: Iterable[int]) -> dict[int, str]:
    """Назначает участникам разные эмодзи, не меняя их при повторном открытии.

    Эмодзи зависит от комнаты и идентификатора пользователя. Если два
    предпочтения совпадают, свободный значок выбирается детерминированно
    по возрастанию идентификаторов. Поэтому один и тот же пользователь
    получает одинаковый значок при повторных запросах списка.

    Args:
        room_id: Комната, в рамках которой нужны различимые обозначения.
        user_ids: Идентификаторы участников, включая скрытых.

    Returns:
        Соответствие идентификатора пользователя и выбранного эмодзи.
    """
    ordered_ids = sorted(set(user_ids))
    result: dict[int, str] = {}
    taken: set[int] = set()
    for user_id in ordered_ids:
        if len(taken) >= len(ANONYMOUS_EMOJIS):
            result[user_id] = FALLBACK_EMOJI
            continue
        digest = hashlib.sha256(f"{room_id}:{user_id}".encode()).digest()
        index = int.from_bytes(digest[:8], "big") % len(ANONYMOUS_EMOJIS)
        while index in taken:
            index = (index + 1) % len(ANONYMOUS_EMOJIS)
        taken.add(index)
        result[user_id] = ANONYMOUS_EMOJIS[index]
    return result
