"""
Context processors для клубного раздела: клубный контекст в шапке ЛК.
"""

from typing import Any

from .models import ClubMember, ClubMemberStatus
from .navigation import build_club_navigation_entry
from .plan_services import get_member_active_plan
from .services import get_fee_status_for_member


def club_context(request: Any) -> dict[str, Any]:
    """
    Добавляет в контекст шаблонов данные о клубах пользователя и текущем выбранном клубе.
    """
    out: dict[str, Any] = {
        "user_clubs": [],
        "club_switcher_items": [],
        "current_club": None,
        "current_club_member": None,
        "current_club_member_plan": None,
        "current_club_fee_status": None,
    }
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return out

    memberships = list(
        ClubMember.objects.filter(
            user=request.user,
            status=ClubMemberStatus.ACTIVE,
        )
        .select_related("club")
        .order_by("club__name")
    )
    if not memberships:
        return out

    clubs = [m.club for m in memberships]
    out["user_clubs"] = clubs

    current_slug = request.session.get("current_club_slug")
    current_member = None
    if current_slug:
        for m in memberships:
            if m.club.slug == current_slug:
                current_member = m
                break
    if not current_member:
        current_member = memberships[0]
        request.session["current_club_slug"] = current_member.club.slug

    out["current_club"] = current_member.club
    out["current_club_member"] = current_member
    out["current_club_member_plan"] = get_member_active_plan(current_member)
    out["current_club_fee_status"] = get_fee_status_for_member(
        current_member.club, current_member
    )
    out["club_switcher_items"] = [
        build_club_navigation_entry(member, current_slug=current_member.club.slug)
        for member in memberships
    ]
    return out
