import csv
import secrets
from io import StringIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.uploadedfile import UploadedFile
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.users.models import Notification

from ..forms import ClubInviteLinkForm, InviteByEmailForm
from ..models import (
    Club,
    ClubInviteLink,
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubMember,
    ClubMemberRole,
    ClubMemberStatus,
    ClubRating,
)
from ..notifications import send_club_invite_email, send_new_member_notification
from ..services import club_can_add_member, club_is_operational, user_can_manage_club
from .helpers import _get_club_and_check_manage, _resolve_club_manage, logger


@login_required
@require_http_methods(["GET", "POST"])
def invite_create(request: HttpRequest, slug: str) -> HttpResponse:
    """Создание инвайт-ссылки (админ/менеджер клуба)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_club(request.user, club):
        messages.error(request, "Нет доступа.")
        return redirect("clubs:club_public_detail", slug=slug)
    if not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return redirect("clubs:club_public_detail", slug=slug)

    if request.method == "POST":
        form = ClubInviteLinkForm(request.POST)
        if form.is_valid():
            expires_days = form.cleaned_data.get("expires_days")
            max_uses = form.cleaned_data.get("max_uses")
            expires_at = None
            if expires_days:
                expires_at = timezone.now() + timezone.timedelta(days=expires_days)
            token = secrets.token_urlsafe(32)[:64]
            link = ClubInviteLink.objects.create(
                club=club,
                token=token,
                created_by=request.user,
                expires_at=expires_at,
                max_uses=max_uses or None,
                is_active=True,
            )
            join_path = reverse("clubs:join", kwargs={"slug": club.slug})
            full_url = request.build_absolute_uri(f"{join_path}?token={link.token}")
            messages.success(
                request, "Ссылка создана. Скопируйте и отправьте участникам."
            )
            return render(
                request,
                "clubs/invite_created.html",
                {"club": club, "link": link, "full_url": full_url},
            )
    else:
        form = ClubInviteLinkForm()

    return render(
        request,
        "clubs/invite_create.html",
        {"club": club, "form": form, "is_club_panel": True},
    )


@login_required
@require_POST
def join_request_approve(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Одобрить заявку на вступление в клуб."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

    join_request = get_object_or_404(
        ClubJoinRequest.objects.select_related("user", "club"),
        pk=pk,
        club=club,
        status=ClubJoinRequestStatus.PENDING,
    )

    member, created = ClubMember.objects.get_or_create(
        club=club,
        user=join_request.user,
        defaults={
            "role": ClubMemberRole.PLAYER,
            "status": ClubMemberStatus.ACTIVE,
            "invited_by": request.user,
            "joined_at": timezone.now(),
        },
    )
    if not created:
        member.role = ClubMemberRole.PLAYER
        member.status = ClubMemberStatus.ACTIVE
        member.invited_by = request.user
        member.joined_at = timezone.now()
        member.save(update_fields=["role", "status", "invited_by", "joined_at"])

    ClubRating.objects.get_or_create(club=club, member=member, defaults={"points": 0})

    join_request.status = ClubJoinRequestStatus.APPROVED
    join_request.reviewed_by = request.user
    join_request.reviewed_at = timezone.now()
    join_request.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"]
    )

    Notification.objects.create(
        user=join_request.user,
        message=f"Ваша заявка на вступление в клуб «{club.name}» одобрена.",
        url=reverse("clubs:club_public_detail", kwargs={"slug": club.slug}),
    )

    try:
        dashboard_url = request.build_absolute_uri(
            reverse("clubs:dashboard", kwargs={"slug": club.slug})
        )
        send_new_member_notification(club, member, dashboard_url=dashboard_url)
    except Exception:
        logger.exception("Ошибка отправки уведомления о новом участнике")

    messages.success(request, "Заявка одобрена. Игрок добавлен в клуб.")
    return redirect("clubs:invites_list", slug=slug)


@login_required
@require_POST
def join_request_reject(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Отклонить заявку на вступление в клуб."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

    join_request = get_object_or_404(
        ClubJoinRequest.objects.select_related("user", "club"),
        pk=pk,
        club=club,
        status=ClubJoinRequestStatus.PENDING,
    )
    join_request.status = ClubJoinRequestStatus.REJECTED
    join_request.reviewed_by = request.user
    join_request.reviewed_at = timezone.now()
    join_request.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"]
    )

    Notification.objects.create(
        user=join_request.user,
        message=f"Ваша заявка на вступление в клуб «{club.name}» отклонена.",
        url=reverse("clubs:club_public_detail", kwargs={"slug": club.slug}),
    )

    messages.success(request, "Заявка отклонена.")
    return redirect("clubs:invites_list", slug=slug)


@login_required
@require_GET
def invitations_list(request: HttpRequest) -> HttpResponse:
    """Список входящих приглашений в клубы."""
    invites = (
        ClubMember.objects.filter(user=request.user, status=ClubMemberStatus.INVITED)
        .select_related("club", "invited_by")
        .order_by("-created_at")
    )
    return render(request, "clubs/invitations_list.html", {"invites": invites})


@login_required
@require_POST
def invitation_accept(request: HttpRequest, pk: int) -> HttpResponse:
    """Принять приглашение в клуб."""
    member = get_object_or_404(
        ClubMember.objects.select_related("club"),
        pk=pk,
        user=request.user,
        status=ClubMemberStatus.INVITED,
    )
    member.status = ClubMemberStatus.ACTIVE
    member.joined_at = timezone.now()
    member.save(update_fields=["status", "joined_at"])

    ClubRating.objects.get_or_create(
        club=member.club, member=member, defaults={"points": 0}
    )

    try:
        dashboard_url = request.build_absolute_uri(
            reverse("clubs:dashboard", kwargs={"slug": member.club.slug})
        )
        send_new_member_notification(member.club, member, dashboard_url=dashboard_url)
    except Exception:
        logger.exception("Ошибка отправки уведомления о новом участнике")

    messages.success(request, f"Вы вступили в клуб «{member.club.name}».")
    return redirect("clubs:invitations_list")


@login_required
@require_POST
def invitation_decline(request: HttpRequest, pk: int) -> HttpResponse:
    """Отклонить приглашение в клуб."""
    member = get_object_or_404(
        ClubMember.objects.select_related("club"),
        pk=pk,
        user=request.user,
        status=ClubMemberStatus.INVITED,
    )
    member.status = ClubMemberStatus.REMOVED
    member.save(update_fields=["status"])
    messages.success(request, "Приглашение отклонено.")
    return redirect("clubs:invitations_list")


@login_required
@require_http_methods(["GET", "POST"])
def invite_by_email(request: HttpRequest, slug: str) -> HttpResponse:
    """Пригласить игрока по email (создать ClubMember status=invited)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_club(request.user, club):
        messages.error(request, "Нет доступа.")
        return redirect("clubs:club_public_detail", slug=slug)
    if not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return redirect("clubs:club_public_detail", slug=slug)

    if request.method == "POST":
        form = InviteByEmailForm(request.POST)
        if form.is_valid():
            from django.contrib.auth import get_user_model

            User = get_user_model()
            email = form.cleaned_data["email"].strip().lower()
            user = User.objects.filter(email__iexact=email).first()
            if not user:
                messages.warning(
                    request,
                    f"Пользователь с email {email} не найден. Отправьте ему ссылку на регистрацию.",
                )
                return redirect("clubs:invite_by_email", slug=slug)
            existing_member = club.members.filter(user=user).first()
            if existing_member:
                if existing_member.status in (
                    ClubMemberStatus.ACTIVE,
                    ClubMemberStatus.INVITED,
                ):
                    messages.warning(
                        request, "Этот пользователь уже в клубе или приглашён."
                    )
                    return redirect("clubs:invite_by_email", slug=slug)
                existing_member.status = ClubMemberStatus.INVITED
                existing_member.role = ClubMemberRole.PLAYER
                existing_member.invited_by = request.user
                existing_member.save(
                    update_fields=["status", "role", "invited_by", "joined_at"]
                )
            else:
                can_add, limit_msg = club_can_add_member(club)
                if not can_add:
                    messages.error(request, limit_msg)
                    return redirect("clubs:invite_by_email", slug=slug)
                ClubMember.objects.create(
                    club=club,
                    user=user,
                    role=ClubMemberRole.PLAYER,
                    status=ClubMemberStatus.INVITED,
                    invited_by=request.user,
                )

            try:
                accept_url = request.build_absolute_uri(
                    reverse("clubs:invitations_list")
                )
                player_name = user.get_full_name() or email
                send_club_invite_email(club, player_name, email, accept_url)
            except Exception:
                logger.exception("Ошибка отправки email-приглашения в клуб")

            messages.success(request, f"Приглашение отправлено на {email}.")
            return redirect("clubs:dashboard", slug=slug)
    else:
        form = InviteByEmailForm()

    return render(
        request,
        "clubs/invite_by_email.html",
        {"club": club, "form": form, "is_club_panel": True},
    )


@login_required
@require_http_methods(["GET", "POST"])
def invite_import_csv(request: HttpRequest, slug: str) -> HttpResponse:
    """Импорт приглашений из CSV (по одному email на строку)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_club(request.user, club):
        messages.error(request, "Нет доступа.")
        return redirect("clubs:club_public_detail", slug=slug)
    if not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return redirect("clubs:club_public_detail", slug=slug)

    if request.method == "POST":
        file: UploadedFile | None = request.FILES.get("file")
        if not file or not file.name.lower().endswith((".csv", ".txt")):
            messages.error(request, "Загрузите CSV или TXT с email в каждой строке.")
            return redirect("clubs:invite_import_csv", slug=slug)
        from django.contrib.auth import get_user_model

        User = get_user_model()
        content = file.read().decode("utf-8", errors="ignore")
        reader = csv.reader(StringIO(content))
        invited = 0
        not_found: list[str] = []
        already = 0
        for row in reader:
            if not row:
                continue
            email = (row[0].strip() if row else "").strip().lower()
            if not email or "@" not in email:
                continue
            user = User.objects.filter(email__iexact=email).first()
            if not user:
                not_found.append(email)
                continue
            if club.members.filter(user=user).exists():
                already += 1
                continue
            can_add, limit_msg = club_can_add_member(club)
            if not can_add:
                messages.error(request, limit_msg)
                return redirect("clubs:invite_import_csv", slug=slug)
            ClubMember.objects.create(
                club=club,
                user=user,
                role=ClubMemberRole.PLAYER,
                status=ClubMemberStatus.INVITED,
                invited_by=request.user,
            )
            invited += 1
        msg = f"Добавлено приглашений: {invited}."
        if already:
            msg += f" Уже в клубе/приглашены: {already}."
        if not_found:
            preview = ", ".join(not_found[:10])
            tail = " …" if len(not_found) > 10 else ""
            msg += f" Не найдены: {preview}{tail}"
        messages.success(request, msg)
        return redirect("clubs:invites_list", slug=slug)

    return render(
        request,
        "clubs/invite_import_csv.html",
        {"club": club, "is_club_panel": True},
    )


@login_required
@require_GET
def invites_list(request: HttpRequest, slug: str) -> HttpResponse:
    """Список инвайт-ссылок клуба."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    links = list(
        ClubInviteLink.objects.filter(club=club)
        .select_related("created_by")
        .order_by("-created_at")
    )
    for link in links:
        link.full_join_url = request.build_absolute_uri(
            reverse("clubs:join", kwargs={"slug": club.slug}) + "?token=" + link.token
        )
    join_requests = list(
        ClubJoinRequest.objects.filter(club=club)
        .select_related("user", "user__player", "reviewed_by")
        .order_by("-created_at")
    )
    return render(
        request,
        "clubs/invites_list.html",
        {
            "club": club,
            "links": links,
            "join_requests": join_requests,
            "is_club_panel": True,
        },
    )


@login_required
@require_POST
def invite_deactivate(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Деактивировать инвайт-ссылу."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    link = get_object_or_404(ClubInviteLink, pk=pk, club=club)
    link.is_active = False
    link.save(update_fields=["is_active"])
    messages.success(request, "Ссылка деактивирована.")
    return redirect("clubs:invites_list", slug=slug)
