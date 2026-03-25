"""
Утилиты фиксации согласий пользователей (платформа и оферты клубов).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from django.http import HttpRequest

from apps.core.models import UserConsent

if TYPE_CHECKING:
    from apps.clubs.models import Club


def _get_client_ip(request: HttpRequest) -> str | None:
    """Возвращает IP клиента с учётом прокси (X-Forwarded-For)."""
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    remote = (request.META.get("REMOTE_ADDR") or "").strip()
    return remote or None


def record_platform_consent(
    request: HttpRequest,
    consent_type: str,
    version: str,
) -> tuple[UserConsent, bool]:
    """Создаёт запись согласия с документом платформы, если такой ещё нет.

    Args:
        request: HTTP-запрос (пользователь должен быть аутентифицирован).
        consent_type: Значение ``UserConsent.ConsentType`` (строка).
        version: Версия документа (хэш, номер редакции и т.п.).

    Returns:
        Кортеж ``(объект UserConsent, создан ли новый)``.
    """
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:500]
    return cast(
        tuple[UserConsent, bool],
        UserConsent.objects.get_or_create(
            user=request.user,
            consent_type=consent_type,
            club=None,
            document_version=version,
            defaults={
                "ip_address": _get_client_ip(request),
                "user_agent": ua,
            },
        ),
    )


def record_club_consent(request: HttpRequest, club: Club) -> tuple[UserConsent, bool]:
    """Фиксирует согласие с офертой клуба (версия из ``ClubLegalDocument``).

    Args:
        request: HTTP-запрос.
        club: Клуб, чья оферта акцептована.

    Returns:
        Кортеж ``(объект UserConsent, создан ли новый)``.
    """
    from apps.clubs.models import ClubLegalDocument

    doc = ClubLegalDocument.objects.filter(club=club).first()
    version = doc.version if doc else "1.0"
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:500]
    return cast(
        tuple[UserConsent, bool],
        UserConsent.objects.get_or_create(
            user=request.user,
            consent_type=UserConsent.ConsentType.CLUB_OFFER,
            club=club,
            document_version=version,
            defaults={
                "ip_address": _get_client_ip(request),
                "user_agent": ua,
            },
        ),
    )
