"""Подготовка ленты уведомлений для страницы личного кабинета."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from apps.users.models import Notification

KIND_LABELS: dict[str, str] = {
    "sparring": "Спарринг",
    "tournament": "Турнир",
    "match": "Матч",
    "club": "Клуб",
    "training": "Тренировка",
    "general": "Событие",
}

_KIND_ORDER: tuple[str, ...] = (
    "sparring",
    "tournament",
    "match",
    "club",
    "training",
    "general",
)


@dataclass(frozen=True, slots=True)
class InboxCard:
    """Карточка уведомления для шаблона ленты.

    Attributes:
        message: Текст уведомления.
        url: Безопасная ссылка или пустая строка.
        created_at: Момент создания.
        kind: Ключ группы.
        kind_label: Подпись группы на русском.
        was_unread: Уведомление было непрочитанным в момент открытия страницы.
    """

    message: str
    url: str
    created_at: datetime
    kind: str
    kind_label: str
    was_unread: bool


def classify_notification(message: str) -> str:
    """Определить группу уведомления по тексту сообщения.

    Args:
        message: Текст уведомления.

    Returns:
        str: Ключ группы: sparring, tournament, match, club, training или general.
    """
    text = message.casefold()
    if "спарринг" in text:
        return "sparring"
    if "клуб" in text:
        return "club"
    if "трениров" in text:
        return "training"
    if "турнир" in text:
        return "tournament"
    if any(token in text for token in ("матч", "результат", "дедлайн")):
        return "match"
    return "general"


def safe_notification_url(url: str) -> str:
    """Оставить только относительный путь или обычную http(s)-ссылку.

    Args:
        url: Значение поля Notification.url.

    Returns:
        str: Ссылка для href либо пустая строка.
    """
    cleaned = url.strip()
    if cleaned.startswith("/") and not cleaned.startswith("//"):
        return cleaned
    lowered = cleaned.casefold()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return cleaned
    return ""


def build_inbox_context(
    notes: Sequence[Notification],
    unread_ids: set[int],
) -> dict[str, Any]:
    """Собрать контекст шаблона ленты уведомлений.

    Args:
        notes: Уведомления пользователя, новые сверху.
        unread_ids: Идентификаторы, которые были непрочитанными до отметки.

    Returns:
        dict[str, Any]: Карточки, сводка и непустые группы для фильтра.
    """
    cards = [
        InboxCard(
            message=note.message,
            url=safe_notification_url(note.url),
            created_at=note.created_at,
            kind=(kind := classify_notification(note.message)),
            kind_label=KIND_LABELS[kind],
            was_unread=note.pk in unread_ids,
        )
        for note in notes
    ]
    counts = {kind: 0 for kind in _KIND_ORDER}
    for card in cards:
        counts[card.kind] += 1
    groups = [
        {"kind": kind, "label": KIND_LABELS[kind], "count": counts[kind]}
        for kind in _KIND_ORDER
        if counts[kind] > 0
    ]
    return {
        "inbox_cards": cards,
        "inbox_total": len(cards),
        "inbox_unread": sum(1 for card in cards if card.was_unread),
        "inbox_latest": cards[0].created_at if cards else None,
        "inbox_groups": groups,
    }
