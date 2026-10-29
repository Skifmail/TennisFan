"""Создание аккаунта непосредственно из подтверждённого профиля соцсети."""

from __future__ import annotations

from datetime import date

from django.http import HttpRequest

from .models import Player, SocialIdentity, User


def register_social_user(
    request: HttpRequest, provider: str, identity: dict[str, str]
) -> tuple[User, Player]:
    """Создать неподтверждённого игрока без вымышленных почты или уровня силы."""
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
    return user, player
