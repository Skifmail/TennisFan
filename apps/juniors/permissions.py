"""Серверные проверки полномочий на каждый детский профиль."""

from typing import cast

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db.models import Q, QuerySet
from django.http import HttpRequest

from apps.users.models import User

from .models import ChildProfile, GuardianLink, JuniorMatch, JuniorTournament


def account(request: HttpRequest) -> User:
    """Возвращает активный пользовательский аккаунт после проверки типа и авторизации."""
    user = request.user
    if not isinstance(user, User) or not user.is_active:
        raise PermissionDenied("Войдите в аккаунт законного представителя.")
    return user


def guardian_for(
    user: User, child: ChildProfile, *, verified: bool = True
) -> GuardianLink:
    """Возвращает действующую связь, не доверяя переключателю в интерфейсе."""
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied("Требуется вход в аккаунт представителя.")
    allowed = (
        [GuardianLink.Status.VERIFIED]
        if verified
        else [
            GuardianLink.Status.VERIFIED,
            GuardianLink.Status.PENDING,
            GuardianLink.Status.REJECTED,
        ]
    )
    link = GuardianLink.objects.filter(
        child=child, user=user, status__in=allowed
    ).first()
    if link is None:
        raise PermissionDenied("Нет полномочий на управление этим профилем.")
    return cast(GuardianLink, link)


def require_verifier(user: User) -> None:
    """Проверяет отдельное право проверки представителей."""
    if (
        not user.is_active
        or not user.is_staff
        or not user.has_perm("juniors.verify_representative")
    ):
        raise PermissionDenied("Нет права проверки представителей.")


def require_organizer(user: User, tournament: JuniorTournament) -> None:
    """Допускает ответственного организатора либо суперпользователя."""
    if not user.is_active:
        raise PermissionDenied("Нет права проведения турнира.")
    if user.is_superuser and user.is_staff:
        return
    if tournament.club_id:
        from apps.clubs.models import ClubMember

        if ClubMember.objects.filter(
            club_id=tournament.club_id,
            user=user,
            role__in=["admin", "manager"],
            status="active",
        ).exists():
            return
        raise PermissionDenied("Нет действующих полномочий организатора в этом клубе.")
    if (
        not user.is_staff
        or tournament.organizer_id != user.pk
        or not user.has_perm("juniors.manage_competition")
    ):
        raise PermissionDenied("Нет права проведения этого турнира.")


def accessible_competitions(user: User) -> QuerySet[JuniorTournament]:
    """Ограничивает списки организатора, включая прекращение клубных полномочий."""
    qs = JuniorTournament.objects.all()
    if user.is_superuser:
        return qs
    from apps.clubs.models import ClubMember

    clubs = ClubMember.objects.filter(
        user=user, role__in=["admin", "manager"], status="active"
    ).values_list("club_id", flat=True)
    return qs.filter(Q(club_id__in=clubs) | Q(club__isnull=True, organizer=user))


def can_manage_competition(
    user: User | AnonymousUser, tournament: JuniorTournament
) -> bool:
    """Проверяет доступ к кнопке управления по тем же правилам, что и сервер."""
    if not isinstance(user, User):
        return False
    try:
        require_organizer(user, tournament)
    except PermissionDenied:
        return False
    return True


def match_guardians(user: User, match: "JuniorMatch") -> list[GuardianLink]:
    """Возвращает проверенные полномочия взрослого на участвующих в матче детей."""
    if not user.is_authenticated or not user.is_active:
        return []
    child_ids = [
        entry.child_id
        for entry in (match.entry1, match.entry2)
        if entry and entry.status == "confirmed"
    ]
    return list(
        GuardianLink.objects.filter(
            user=user, child_id__in=child_ids, status="verified", child__status="active"
        ).select_related("child")
    )
