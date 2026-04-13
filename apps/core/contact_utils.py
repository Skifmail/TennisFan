"""Утилиты нормализации и отображения контактных данных."""

from __future__ import annotations


def normalize_russian_phone(value: str | None) -> str | None:
    """Нормализует российский номер телефона к формату +7XXXXXXXXXX."""
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    elif digits.startswith("7") and len(digits) == 11:
        pass
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return None
    return f"+{digits}"


def build_whatsapp_url(value: str | None) -> str | None:
    """Возвращает ссылку wa.me для переданного номера телефона."""
    phone = normalize_russian_phone(value)
    if not phone:
        return None
    return f"https://wa.me/{phone.lstrip('+')}"


def build_telegram_url(value: str | None) -> str | None:
    """Возвращает ссылку на Telegram по username."""
    if not value:
        return None
    username = value.strip().lstrip("@")
    return f"https://t.me/{username}" if username else None


def normalize_max_contact(value: str | None) -> str:
    """Нормализует контакт MAX, сохраняя ссылку или приводя номер к формату +7."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    phone = normalize_russian_phone(raw)
    if phone:
        return phone
    return raw


def build_max_url(value: str | None) -> str | None:
    """Возвращает URL для MAX, если контакт задан ссылкой."""
    contact = normalize_max_contact(value)
    return contact if contact.startswith(("http://", "https://")) else None


def get_max_display_contact(value: str | None) -> str | None:
    """Возвращает нормализованное отображаемое значение контакта MAX."""
    contact = normalize_max_contact(value)
    return contact or None
