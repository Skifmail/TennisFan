from __future__ import annotations

import logging
from typing import Final, cast

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)

_MASK: Final[str] = "••••••••"


def encrypt_secret(plain: str) -> str:
    """Зашифровать секретный ключ клуба для хранения в БД.

    Args:
        plain (str): Исходное значение секрета (API-ключ ЮKassa и т.п.).

    Returns:
        str: Зашифрованная строка, пригодная для хранения в текстовом поле.
             При отсутствии ключа шифрования возвращает исходное значение.

    Raises:
        ValueError: Если передана пустая строка.
    """
    value = plain.strip()
    if not value:
        raise ValueError("Пустой секретный ключ не может быть зашифрован.")

    key = getattr(settings, "CLUB_PAYMENT_ENCRYPTION_KEY", "").strip()
    if not key:
        logger.warning(
            "CLUB_PAYMENT_ENCRYPTION_KEY не задан, секрет клуба будет сохранён без шифрования."
        )
        return value

    fernet = Fernet(key.encode("ascii"))
    token = fernet.encrypt(value.encode("utf-8"))
    # Явно приводим тип, чтобы mypy не воспринимал результат как Any.
    return cast(str, token.decode("ascii"))


def decrypt_secret(encrypted: str) -> str:
    """Расшифровать секретный ключ клуба из БД.

    Args:
        encrypted (str): Хранимое значение (зашифрованное или открытый текст).

    Returns:
        str: Расшифрованный секретный ключ в открытом виде.
    """
    value = encrypted.strip()
    if not value:
        return ""

    key = getattr(settings, "CLUB_PAYMENT_ENCRYPTION_KEY", "").strip()
    if not key:
        logger.warning(
            "CLUB_PAYMENT_ENCRYPTION_KEY не задан, возвращаем секрет клуба как есть."
        )
        return value

    try:
        fernet = Fernet(key.encode("ascii"))
        decrypted = fernet.decrypt(value.encode("ascii"))
        # Явно приводим тип, чтобы избежать возврата Any.
        return cast(str, decrypted.decode("utf-8"))
    except (InvalidToken, ValueError):
        # Если данные не были зашифрованы или ключ поменяли — не ломаем поток,
        # а возвращаем значение как есть.
        logger.warning(
            "Не удалось расшифровать секрет клуба, возвращаем исходное значение."
        )
        return value


def get_secret_mask() -> str:
    """Получить маску для отображения сохранённого секрета в формах.

    Returns:
        str: Строка-маска фиксированной длины.
    """
    return _MASK
