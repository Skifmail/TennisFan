"""Вспомогательные функции для правовых документов сайта."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

from django.conf import settings

DOCS_ROOT: Final = Path(settings.BASE_DIR) / "static" / "documents"
LEGAL_VERSION_LENGTH: Final = 12

LEGAL_DOCUMENT_PATHS: Final[dict[str, Path]] = {
    "personal-data": DOCS_ROOT / "personal_data.txt",
    "privacy": DOCS_ROOT / "privacy.txt",
    "offer": DOCS_ROOT / "public_offer.txt",
    "terms": DOCS_ROOT / "user_agreement.txt",
    "club-organizer-rules": DOCS_ROOT / "club_organizer_rules.txt",
    "fan-token": DOCS_ROOT / "fan_token.txt",
    "chat-rules": DOCS_ROOT / "chat_rules.txt",
    "chat-personal-data": DOCS_ROOT / "chat_personal_data.txt",
    "chat-disclosure": DOCS_ROOT / "chat_disclosure.txt",
}


def get_legal_document_version(slug: str) -> str:
    """Вернуть версию юридического документа по его содержимому.

    Для фиксации согласий используется короткий SHA-256 хэш от текущего текста
    документа. Это позволяет в админке и журналах понять, с какой именно
    редакцией согласился пользователь.

    Args:
        slug (str): Идентификатор документа.

    Returns:
        str: Короткая версия документа или ``"unknown"``, если документ не найден.
    """
    path = LEGAL_DOCUMENT_PATHS.get(slug)
    if path is None or not path.exists():
        return "unknown"

    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest()[:LEGAL_VERSION_LENGTH]
