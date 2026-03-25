import csv
import secrets
from io import StringIO
from typing import cast

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.users.models import Notification

from ..forms import ClubInviteImportForm, ClubInviteLinkForm, InviteByEmailForm
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
from ..services import club_can_add_member
from .helpers import _get_club_and_check_manage, _resolve_club_manage, logger

INVITE_ACTION_CREATE_LINK = "create_link"
INVITE_ACTION_INVITE_EMAIL = "invite_email"
INVITE_ACTION_IMPORT_CSV = "import_csv"


def _get_invites_page_url(slug: str, anchor: str = "") -> str:
    """Возвращает URL страницы приглашений клуба с необязательным якорем."""
    url = cast(str, reverse("clubs:invites_list", kwargs={"slug": slug}))
    if anchor:
        return f"{url}#{anchor}"
    return url


def _create_invite_link(
    request: HttpRequest,
    club: Club,
    form: ClubInviteLinkForm,
) -> ClubInviteLink:
    """Создаёт новую инвайт-ссылку клуба из валидной формы."""
    expires_days = form.cleaned_data.get("expires_days")
    max_uses = form.cleaned_data.get("max_uses")
    expires_at = None
    if expires_days:
        expires_at = timezone.now() + timezone.timedelta(days=expires_days)
    token = secrets.token_urlsafe(32)[:64]
    link = cast(
        ClubInviteLink,
        ClubInviteLink.objects.create(
            club=club,
            token=token,
            created_by=request.user,
            expires_at=expires_at,
            max_uses=max_uses or None,
            is_active=True,
        ),
    )
    return link


def _process_invite_link_form(
    request: HttpRequest,
    club: Club,
    form: ClubInviteLinkForm,
) -> bool:
    """Обрабатывает inline-форму создания инвайт-ссылки."""
    if not form.is_valid():
        return False
    _create_invite_link(request, club, form)
    messages.success(request, "Ссылка создана. Она уже появилась в списке ниже.")
    return True


def _process_invite_email_form(
    request: HttpRequest,
    club: Club,
    form: InviteByEmailForm,
) -> bool:
    """Обрабатывает inline-форму приглашения игрока по email."""
    if not form.is_valid():
        return False

    user_model = get_user_model()
    email = form.cleaned_data["email"].strip().lower()
    user = user_model.objects.filter(email__iexact=email).first()
    if not user:
        form.add_error(
            "email",
            f"Пользователь с email {email} не найден. Отправьте ему ссылку на регистрацию.",
        )
        return False

    existing_member = club.members.filter(user=user).first()
    if existing_member:
        if existing_member.status in (
            ClubMemberStatus.ACTIVE,
            ClubMemberStatus.INVITED,
        ):
            form.add_error("email", "Этот пользователь уже в клубе или приглашён.")
            return False
        existing_member.status = ClubMemberStatus.INVITED
        existing_member.role = ClubMemberRole.PLAYER
        existing_member.invited_by = request.user
        existing_member.save(update_fields=["status", "role", "invited_by"])
    else:
        can_add, limit_msg = club_can_add_member(club)
        if not can_add:
            form.add_error(None, limit_msg)
            return False
        ClubMember.objects.create(
            club=club,
            user=user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.INVITED,
            invited_by=request.user,
        )

    try:
        accept_url = request.build_absolute_uri(reverse("clubs:invitations_list"))
        player_name = user.get_full_name() or email
        send_club_invite_email(club, player_name, email, accept_url)
    except Exception:
        logger.exception("Ошибка отправки email-приглашения в клуб")

    messages.success(request, f"Приглашение отправлено на {email}.")
    return True


def _process_invite_import_form(
    request: HttpRequest,
    club: Club,
    form: ClubInviteImportForm,
) -> bool:
    """Обрабатывает inline-форму массового импорта приглашений."""
    if not form.is_valid():
        return False

    user_model = get_user_model()
    file = form.cleaned_data["file"]
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
        user = user_model.objects.filter(email__iexact=email).first()
        if not user:
            not_found.append(email)
            continue
        if club.members.filter(user=user).exists():
            already += 1
            continue
        can_add, limit_msg = club_can_add_member(club)
        if not can_add:
            if invited:
                messages.warning(
                    request,
                    f"Импорт остановлен после {invited} приглашений.",
                )
            form.add_error(None, limit_msg)
            return False
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
    return True


def _build_invites_page_context(
    request: HttpRequest,
    club: Club,
    *,
    invite_link_form: ClubInviteLinkForm | None = None,
    invite_email_form: InviteByEmailForm | None = None,
    invite_import_form: ClubInviteImportForm | None = None,
    active_invite_action: str = "",
) -> dict[str, object]:
    """Собирает контекст страницы приглашений клуба."""
    now = timezone.now()
    links = list(
        ClubInviteLink.objects.filter(club=club)
        .select_related("created_by")
        .order_by("-created_at")
    )
    active_links_count = 0
    total_link_uses = 0
    for link in links:
        link.full_join_url = request.build_absolute_uri(
            reverse("clubs:join", kwargs={"slug": club.slug}) + "?token=" + link.token
        )
        link.created_by_display = (
            link.created_by.get_full_name() or link.created_by.email
        )
        link.is_expired = bool(link.expires_at and link.expires_at <= now)
        link.is_limit_reached = (
            link.max_uses is not None and link.use_count >= link.max_uses
        )
        link.can_activate = (
            not link.is_active and not link.is_expired and not link.is_limit_reached
        )
        link.can_delete = not link.is_active or link.is_expired or link.is_limit_reached
        total_link_uses += link.use_count

        if link.is_expired:
            link.status_label = "Истекла"
            link.status_badge_class = "club-members-badge--warning"
            if link.is_active:
                link.status_note = "Срок действия завершился."
            else:
                link.status_note = "Срок действия завершился. Повторно включить нельзя."
        elif link.is_limit_reached:
            link.status_label = "Лимит исчерпан"
            link.status_badge_class = "club-members-badge--warning"
            if link.is_active:
                link.status_note = "Создайте новую ссылку для вступления."
            else:
                link.status_note = (
                    "Лимит использований достигнут. Повторно включить нельзя."
                )
        elif not link.is_active:
            link.status_label = "Отключена"
            link.status_badge_class = "club-members-badge--danger"
            link.status_note = "Ссылка выключена вручную. Её можно включить снова."
        else:
            link.status_label = "Активна"
            link.status_badge_class = "club-members-badge--success"
            link.status_note = "Готова к отправке игрокам."
            active_links_count += 1

        if link.max_uses is None:
            link.remaining_uses_label = "Без лимита переходов."
        else:
            remaining_uses = max(link.max_uses - link.use_count, 0)
            if remaining_uses:
                link.remaining_uses_label = f"Осталось использований: {remaining_uses}"
            else:
                link.remaining_uses_label = "Лимит использований достигнут."

    join_requests = list(
        ClubJoinRequest.objects.filter(club=club)
        .select_related("user", "user__player", "reviewed_by")
        .order_by("-created_at")
    )
    pending_requests_count = 0
    processed_requests_count = 0
    for join_request in join_requests:
        join_request.user_display_name = (
            join_request.user.get_full_name() or join_request.user.email
        )
        join_request.reviewed_by_display = (
            join_request.reviewed_by.get_full_name() or join_request.reviewed_by.email
            if join_request.reviewed_by
            else ""
        )

        if join_request.status == ClubJoinRequestStatus.PENDING:
            join_request.status_badge_class = "club-members-badge--warning"
            join_request.status_note = "Нужна реакция менеджера."
            pending_requests_count += 1
        elif join_request.status == ClubJoinRequestStatus.APPROVED:
            join_request.status_badge_class = "club-members-badge--success"
            join_request.status_note = "Игрок уже допущен в клуб."
            processed_requests_count += 1
        else:
            join_request.status_badge_class = "club-members-badge--danger"
            join_request.status_note = "Заявка закрыта без вступления."
            processed_requests_count += 1

    return {
        "club": club,
        "links": links,
        "join_requests": join_requests,
        "active_links_count": active_links_count,
        "total_link_uses": total_link_uses,
        "pending_requests_count": pending_requests_count,
        "processed_requests_count": processed_requests_count,
        "invite_link_form": (
            invite_link_form if invite_link_form is not None else ClubInviteLinkForm()
        ),
        "invite_email_form": (
            invite_email_form if invite_email_form is not None else InviteByEmailForm()
        ),
        "invite_import_form": (
            invite_import_form
            if invite_import_form is not None
            else ClubInviteImportForm()
        ),
        "active_invite_action": active_invite_action,
        "is_club_panel": True,
    }


@login_required
@require_GET
def invite_import_template(request: HttpRequest, slug: str) -> HttpResponse:
    """Отдаёт CSV-шаблон для массового импорта приглашений."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["email"])

    response = HttpResponse(
        buffer.getvalue(),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{club.slug}-invite-template.csv"'
    )
    return response


@login_required
@require_http_methods(["GET", "POST"])
def invite_create(request: HttpRequest, slug: str) -> HttpResponse:
    """Создание инвайт-ссылки (админ/менеджер клуба)."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

    if request.method == "GET":
        return redirect(_get_invites_page_url(slug, "invite-action-create"))

    form = ClubInviteLinkForm(request.POST)
    if _process_invite_link_form(request, club, form):
        return redirect("clubs:invites_list", slug=slug)

    messages.error(request, "Проверьте параметры создания ссылки.")
    return redirect(_get_invites_page_url(slug, "invite-action-create"))


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
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

    if request.method == "GET":
        return redirect(_get_invites_page_url(slug, "invite-action-email"))

    form = InviteByEmailForm(request.POST)
    if _process_invite_email_form(request, club, form):
        return redirect("clubs:invites_list", slug=slug)

    messages.error(request, "Проверьте данные для приглашения по email.")
    return redirect(_get_invites_page_url(slug, "invite-action-email"))


@login_required
@require_http_methods(["GET", "POST"])
def invite_import_csv(request: HttpRequest, slug: str) -> HttpResponse:
    """Импорт приглашений из CSV (по одному email на строку)."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

    if request.method == "GET":
        return redirect(_get_invites_page_url(slug, "invite-action-import"))

    form = ClubInviteImportForm(request.POST, request.FILES)
    if _process_invite_import_form(request, club, form):
        return redirect("clubs:invites_list", slug=slug)

    messages.error(request, "Проверьте файл для импорта приглашений.")
    return redirect(_get_invites_page_url(slug, "invite-action-import"))


@login_required
@require_http_methods(["GET", "POST"])
def invites_list(request: HttpRequest, slug: str) -> HttpResponse:
    """Список инвайт-ссылок клуба."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    invite_link_form: ClubInviteLinkForm | None = None
    invite_email_form: InviteByEmailForm | None = None
    invite_import_form: ClubInviteImportForm | None = None
    active_invite_action = ""

    if request.method == "POST":
        action = request.POST.get("invite_action", "").strip()
        active_invite_action = action
        if action == INVITE_ACTION_CREATE_LINK:
            invite_link_form = ClubInviteLinkForm(request.POST)
            if _process_invite_link_form(request, club, invite_link_form):
                return redirect("clubs:invites_list", slug=slug)
        elif action == INVITE_ACTION_INVITE_EMAIL:
            invite_email_form = InviteByEmailForm(request.POST)
            if _process_invite_email_form(request, club, invite_email_form):
                return redirect("clubs:invites_list", slug=slug)
        elif action == INVITE_ACTION_IMPORT_CSV:
            invite_import_form = ClubInviteImportForm(request.POST, request.FILES)
            if _process_invite_import_form(request, club, invite_import_form):
                return redirect("clubs:invites_list", slug=slug)
        else:
            messages.error(request, "Неизвестное действие на странице приглашений.")
            return redirect("clubs:invites_list", slug=slug)

    return render(
        request,
        "clubs/invites_list.html",
        _build_invites_page_context(
            request,
            club,
            invite_link_form=invite_link_form,
            invite_email_form=invite_email_form,
            invite_import_form=invite_import_form,
            active_invite_action=active_invite_action,
        ),
    )


@login_required
@require_POST
def invite_deactivate(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Деактивирует инвайт-ссылку клуба."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    link = get_object_or_404(ClubInviteLink, pk=pk, club=club)
    link.is_active = False
    link.save(update_fields=["is_active"])
    messages.success(request, "Ссылка деактивирована.")
    return redirect("clubs:invites_list", slug=slug)


@login_required
@require_POST
def invite_activate(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Повторно активирует инвайт-ссылку, если она ещё валидна."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    link = get_object_or_404(ClubInviteLink, pk=pk, club=club)
    is_expired = bool(link.expires_at and link.expires_at <= timezone.now())
    is_limit_reached = link.max_uses is not None and link.use_count >= link.max_uses

    if link.is_active:
        messages.warning(request, "Ссылка уже активна.")
        return redirect("clubs:invites_list", slug=slug)
    if is_expired:
        messages.error(request, "Истекшую ссылку нельзя включить снова.")
        return redirect("clubs:invites_list", slug=slug)
    if is_limit_reached:
        messages.error(request, "Ссылку с исчерпанным лимитом нельзя включить снова.")
        return redirect("clubs:invites_list", slug=slug)

    link.is_active = True
    link.save(update_fields=["is_active"])
    messages.success(request, "Ссылка снова активна.")
    return redirect("clubs:invites_list", slug=slug)


@login_required
@require_POST
def invite_delete(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Удаляет ненужную инвайт-ссылку клуба."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    link = get_object_or_404(ClubInviteLink, pk=pk, club=club)
    is_expired = bool(link.expires_at and link.expires_at <= timezone.now())
    is_limit_reached = link.max_uses is not None and link.use_count >= link.max_uses

    if link.is_active and not is_expired and not is_limit_reached:
        messages.error(
            request,
            "Сначала отключите ещё рабочую ссылку, потом удаляйте её.",
        )
        return redirect("clubs:invites_list", slug=slug)

    link.delete()
    messages.success(request, "Ссылка удалена.")
    return redirect("clubs:invites_list", slug=slug)
