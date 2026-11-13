"""Общая нормализация внешних контактов участников спарринга."""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.core.contact_utils import normalize_russian_phone

if TYPE_CHECKING:
    from apps.sparring.models import SparringContact
    from apps.users.models import Player


def build_sparring_contacts(player: Player) -> list[SparringContact]:
    """Собрать контакты игрока без изменения правил доступа к ним.

    Автор и откликнувшийся используют один порядок, нормализацию и формат
    кнопок. Вызывающий код обязан сначала проверить право зрителя на контакты.
    Для работы без дополнительных SQL-запросов пользователь игрока должен
    быть загружен вместе с профилем.

    Args:
        player: Игрок, внешние контакты которого нужны разрешённому зрителю.

    Returns:
        Контакты Telegram, WhatsApp, MAX и телефона, если они заполнены.
    """
    links: list[SparringContact] = []
    username = (player.telegram or "").strip().lstrip("@")
    if player.telegram_url and username:
        links.append(
            {
                "key": "telegram",
                "label": "Telegram",
                "value": f"@{username}",
                "url": player.telegram_url,
                "external": True,
            }
        )
    if player.whatsapp_url and player.whatsapp:
        links.append(
            {
                "key": "whatsapp",
                "label": "WhatsApp",
                "value": player.whatsapp,
                "url": player.whatsapp_url,
                "external": True,
            }
        )
    if player.max_contact_display:
        links.append(
            {
                "key": "max",
                "label": "MAX",
                "value": player.max_contact_display,
                "url": player.max_url or "",
                "external": bool(player.max_url),
            }
        )
    user = getattr(player, "user", None)
    phone = str(getattr(user, "phone", "") or "").strip()
    if phone:
        links.append(
            {
                "key": "phone",
                "label": "Телефон",
                "value": phone,
                "url": f"tel:{normalize_russian_phone(phone) or phone}",
                "external": False,
            }
        )
    return links
