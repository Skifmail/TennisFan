from typing import Any
from urllib.parse import urlencode

from django.urls import reverse

from .models import ClubMember, ClubMemberRole


def build_club_switch_url(*, club_slug: str, next_url: str) -> str:
    """Возвращает URL для переключения текущего клуба с переходом в нужный раздел."""
    switch_url = str(reverse("clubs:set_current_club", kwargs={"slug": club_slug}))
    query_string = str(urlencode({"next": next_url}))
    return switch_url + f"?{query_string}"


def build_club_navigation_entry(
    member: ClubMember,
    *,
    current_slug: str | None = None,
) -> dict[str, Any]:
    """Собирает данные клуба для навигации и переключателя клубов."""
    personal_target = reverse("clubs:my_dashboard")
    public_target = reverse(
        "clubs:club_public_detail",
        kwargs={"slug": member.club.slug},
    )

    if member.role in (ClubMemberRole.ADMIN, ClubMemberRole.MANAGER):
        entry_target = reverse(
            "clubs:dashboard",
            kwargs={"slug": member.club.slug},
        )
        entry_label = "Панель управления"
    else:
        entry_target = personal_target
        entry_label = "Личный кабинет"

    return {
        "club": member.club,
        "entry_label": entry_label,
        "entry_url": build_club_switch_url(
            club_slug=member.club.slug,
            next_url=entry_target,
        ),
        "personal_url": build_club_switch_url(
            club_slug=member.club.slug,
            next_url=personal_target,
        ),
        "open_url": build_club_switch_url(
            club_slug=member.club.slug,
            next_url=public_target,
        ),
        "role_label": member.get_role_display(),
        "is_current": member.club.slug == (current_slug or ""),
    }
