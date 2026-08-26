"""SEO-хелперы для публичных страниц турниров."""

from __future__ import annotations

import json
from typing import Any


def build_tournament_sports_event_json(
    tournament, *, absolute_url: str, site_base_url: str
) -> str:
    """Собрать JSON-LD SportsEvent для страницы турнира.

    Args:
        tournament: Экземпляр ``Tournament``.
        absolute_url: Канонический абсолютный URL страницы.
        site_base_url: Базовый URL сайта без завершающего слэша.

    Returns:
        str: JSON для тега ``application/ld+json``.
    """
    if tournament.court_id:
        location_name = tournament.court.name
        address = tournament.court.address
    elif getattr(tournament, "geo_area_id", None):
        location_name = tournament.geo_area.name
        address = tournament.city or location_name
    else:
        location_name = tournament.city or "Москва и Московская область"
        address = tournament.city or location_name

    if tournament.status == "cancelled":
        event_status = "https://schema.org/EventCancelled"
    else:
        event_status = "https://schema.org/EventScheduled"

    description_parts: list[str] = []
    if getattr(tournament, "start_date_is_pending", False):
        description_parts.append("Старт после набора.")
    if getattr(tournament, "venue_is_pending", False):
        description_parts.append("Место проведения появится после набора.")
    raw_desc = (tournament.description or "").strip()
    if raw_desc:
        # Без HTML, усечём на уровне символов.
        plain = " ".join(raw_desc.split())
        description_parts.append(plain[:200])

    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "SportsEvent",
        "name": tournament.name,
        "url": absolute_url,
        "sport": "Tennis",
        "eventStatus": event_status,
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "description": " ".join(description_parts),
        "location": {
            "@type": "Place",
            "name": location_name,
            "address": address,
        },
        "organizer": {
            "@type": "Organization",
            "name": tournament.club.name if tournament.club_id else "TennisFan",
            "url": site_base_url or absolute_url,
        },
    }

    if (
        not getattr(tournament, "start_date_is_pending", False)
        and tournament.start_date
    ):
        data["startDate"] = tournament.start_date.isoformat()
        if tournament.end_date:
            data["endDate"] = tournament.end_date.isoformat()

    fee = float(tournament.entry_fee or 0)
    if fee > 0:
        try:
            slots = int(tournament.available_slots)
        except Exception:  # noqa: BLE001
            slots = 1
        data["offers"] = {
            "@type": "Offer",
            "price": f"{fee:g}",
            "priceCurrency": "RUB",
            "availability": (
                "https://schema.org/InStock"
                if slots > 0
                else "https://schema.org/SoldOut"
            ),
            "url": absolute_url,
        }

    return json.dumps(data, ensure_ascii=False)
