"""Создание аккаунта непосредственно из подтверждённого профиля соцсети."""

from __future__ import annotations

from datetime import date

from django.http import HttpRequest

from apps.core.consent_utils import record_platform_consent
from apps.core.models import UserConsent
from apps.legal.utils import get_legal_document_version

from .models import Player, SocialIdentity, User


def register_social_user(
    request: HttpRequest, provider: str, identity: dict[str, str]
) -> tuple[User, Player]:
    """Создать неподтверждённого игрока без вымышленных почты или уровня силы."""
    from .views import _log_registration_legal_acceptances

    user = User(
        email=identity.get("email", "").strip().lower() or None,
        first_name=identity.get("first_name", ""),
        last_name=identity.get("last_name", ""),
        phone=identity.get("phone", ""),
        email_verified=False,
    )
    user.set_unusable_password()
    user.save()
    player = Player.objects.create(
        user=user,
        birth_date=(
            date.fromisoformat(identity["birth_date"])
            if identity.get("birth_date")
            else None
        ),
        gender=identity.get("gender", ""),
        city="",
        tennis_entry_level_set=False,
        total_points=0,
        hidden_rating=0,
        is_verified=False,
    )
    SocialIdentity.objects.create(
        user=user, provider=provider, subject=identity["subject"]
    )
    _log_registration_legal_acceptances(request, user)
    previous_user = request.user
    try:
        request.user = user
        for kind, slug in (
            (UserConsent.ConsentType.PLATFORM_OFFER, "offer"),
            (UserConsent.ConsentType.PRIVACY_POLICY, "privacy"),
        ):
            record_platform_consent(
                request, str(kind), get_legal_document_version(slug)
            )
    finally:
        request.user = previous_user
    return user, player
