import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from ..models import Club, ClubMember, ClubMemberRole, ClubMemberStatus
from ..services import user_can_edit_club_settings, user_can_manage_club
from .helpers import (
    _build_club_profile_context,
    _get_club_and_check_manage,
    _resolve_club_manage,
)


@login_required
@require_GET
def api_search_user(request: HttpRequest, slug: str) -> JsonResponse:
    """Поиск пользователя по email (для приглашения в клуб)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_club(request.user, club):
        return JsonResponse({"error": "forbidden"}, status=403)
    email = (request.GET.get("email") or "").strip().lower()
    if not email:
        return JsonResponse({"found": False})
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.filter(email__iexact=email).first()
    if not user:
        return JsonResponse({"found": False})
    existing = club.members.filter(user=user).first()
    if existing:
        return JsonResponse(
            {
                "found": True,
                "email": user.email,
                "already_member": True,
                "status": existing.status,
            }
        )
    return JsonResponse({"found": True, "email": user.email, "id": user.pk})


@login_required
@require_GET
def member_detail(request: HttpRequest, slug: str, member_id: int) -> HttpResponse:
    """Клубный кабинет выбранного участника для управляющих клубом."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

    member = get_object_or_404(
        ClubMember.objects.select_related("user", "user__player"),
        pk=member_id,
        club=club,
    )
    player = getattr(member.user, "player", None)
    if player is None:
        messages.error(request, "У этого участника нет профиля игрока.")
        return redirect("clubs:members_list", slug=slug)

    context = _build_club_profile_context(
        request,
        club=club,
        member=member,
        player=player,
        is_profile_owner=request.user.id == member.user_id,
    )
    context["club_profile_url"] = reverse(
        "clubs:player_profile",
        kwargs={"slug": club.slug, "player_id": player.pk},
    )
    return render(request, "users/profile.html", context)


@login_required
@require_GET
def members_list(request: HttpRequest, slug: str) -> HttpResponse:
    """Список участников клуба с фильтрами и поиском."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    status_filter = request.GET.get("status", "")
    search = (request.GET.get("q") or "").strip()
    members_qs = ClubMember.objects.filter(club=club)
    qs = members_qs.select_related("user", "user__player").order_by("-joined_at")
    if status_filter:
        qs = qs.filter(status=status_filter)
    if search:
        qs = qs.filter(
            Q(user__email__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
        )
    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(
        request,
        "clubs/members_list.html",
        {
            "club": club,
            "is_club_panel": True,
            "page": page,
            "status_filter": status_filter,
            "search": search,
            "members_total": members_qs.count(),
            "members_filtered_count": qs.count(),
            "members_active_count": members_qs.filter(
                status=ClubMemberStatus.ACTIVE
            ).count(),
            "members_invited_count": members_qs.filter(
                status=ClubMemberStatus.INVITED
            ).count(),
            "members_removed_count": members_qs.filter(
                status=ClubMemberStatus.REMOVED
            ).count(),
            "members_admin_count": members_qs.filter(
                status=ClubMemberStatus.ACTIVE,
                role=ClubMemberRole.ADMIN,
            ).count(),
            "members_manager_count": members_qs.filter(
                status=ClubMemberStatus.ACTIVE,
                role=ClubMemberRole.MANAGER,
            ).count(),
            "can_edit_settings": user_can_edit_club_settings(request.user, club),
        },
    )


@login_required
@require_GET
def members_export(request: HttpRequest, slug: str) -> HttpResponse:
    """Экспорт списка участников в CSV."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    members = (
        ClubMember.objects.filter(club=club)
        .select_related("user")
        .order_by("user__email")
    )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="members_{club.slug}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["email", "first_name", "last_name", "role", "status", "joined_at"])
    for member in members:
        writer.writerow(
            [
                member.user.email,
                member.user.first_name or "",
                member.user.last_name or "",
                member.get_role_display(),
                member.get_status_display(),
                member.joined_at.strftime("%Y-%m-%d %H:%M") if member.joined_at else "",
            ]
        )
    return response


@login_required
@require_POST
def member_remove(request: HttpRequest, slug: str, member_id: int) -> HttpResponse:
    """Исключить участника из клуба (status=REMOVED)."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    member = get_object_or_404(ClubMember, pk=member_id, club=club)
    if member.role == ClubMemberRole.ADMIN:
        admin_count = club.members.filter(
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        ).count()
        if admin_count <= 1:
            messages.error(request, "Нельзя исключить единственного администратора.")
            return redirect("clubs:members_list", slug=slug)
    if member.user_id == request.user.id:
        messages.error(request, "Нельзя исключить самого себя.")
        return redirect("clubs:members_list", slug=slug)
    member.status = ClubMemberStatus.REMOVED
    member.save(update_fields=["status"])
    messages.success(request, "Участник исключён из клуба.")
    return redirect("clubs:members_list", slug=slug)
