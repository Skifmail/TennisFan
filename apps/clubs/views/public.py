from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.consent_utils import record_platform_consent
from apps.legal.utils import get_legal_document_version
from apps.tournaments.models import Match, Tournament, TournamentStatus
from apps.users.models import Notification

from ..forms import (
    ClubMemberPlanSelectForm,
    ClubRegistrationStep1Form,
)
from ..models import (
    Club,
    ClubInviteLink,
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubMember,
    ClubMemberRole,
    ClubMemberStatus,
    ClubPlan,
    ClubPlayerPlan,
    ClubRating,
)
from ..notifications import send_new_member_notification
from ..plan_services import assign_member_plan
from ..services import (
    club_can_add_member,
    club_has_public_page_access,
    club_is_operational,
    create_club_with_trial,
    get_joinable_club_catalog,
    get_platform_plans,
    user_can_manage_club,
)
from .helpers import logger


def _annotate_public_tournament_badge(tournament: Tournament) -> None:
    """Заполняет public_badge_status и public_badge_label для карточек на публичных страницах клуба."""
    if tournament.status == TournamentStatus.CANCELLED:
        tournament.public_badge_status = "cancelled"
        tournament.public_badge_label = "ОТМЕНЕН"
    elif tournament.status == TournamentStatus.COMPLETED:
        tournament.public_badge_status = "completed"
        tournament.public_badge_label = "ЗАВЕРШЕН"
    elif (
        tournament.status
        in (
            TournamentStatus.ACTIVE,
            TournamentStatus.GROUP_STAGE,
            TournamentStatus.PLAYOFFS,
        )
        or tournament.bracket_generated
    ):
        tournament.public_badge_status = "active"
        tournament.public_badge_label = "В ИГРЕ"
    else:
        tournament.public_badge_status = "upcoming"
        tournament.public_badge_label = "Идет набор"


def _safe_next_url(request: HttpRequest, fallback: str) -> str:
    """Возвращает безопасный next URL или fallback."""
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback


def _get_plan_prices_for_template() -> dict[str, dict[str, Any]]:
    """Словарь цен по тарифам для register_step2."""
    defaults = {
        "START": {
            "monthly": 990,
            "yearly": 9900,
            "description": "Пробный период 14 дней бесплатно",
        },
        "BASIC": {
            "monthly": 1990,
            "yearly": 19900,
            "description": "Публичная страница, больше игроков",
        },
        "PRO": {
            "monthly": 4990,
            "yearly": 49900,
            "description": "Всё + межклубные турниры",
        },
    }
    result = dict(defaults)
    for plan in get_platform_plans():
        key = plan.slug.upper()
        result[key] = {
            "monthly": float(plan.price_monthly),
            "yearly": float(plan.price_yearly),
            "description": plan.description
            or defaults.get(key, {}).get("description", ""),
        }
    return result


@require_GET
def register_choice(request: HttpRequest) -> HttpResponse:
    """Страница выбора: регистрация как игрок или как клуб."""
    return render(request, "clubs/register_choice.html")


@login_required
@require_GET
def club_discover(request: HttpRequest) -> HttpResponse:
    """Каталог публичных клубов с поиском и заявкой на вступление."""
    search = (request.GET.get("q") or "").strip()
    city = (request.GET.get("city") or "").strip()
    items = get_joinable_club_catalog(request.user, search=search, city=city)
    return render(
        request,
        "clubs/discover.html",
        {
            "clubs_catalog": items,
            "search": search,
            "city": city,
            "results_count": len(items),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def register_step1(request: HttpRequest) -> HttpResponse:
    """Шаг 1 регистрации клуба — данные о клубе."""
    if request.method == "POST":
        form = ClubRegistrationStep1Form(request.POST, request.FILES)
        if form.is_valid():
            session_data = {
                "name": form.cleaned_data["name"],
                "slug": form.get_slug(),
                "city": form.cleaned_data["city"],
                "address": form.cleaned_data["address"],
                "email": form.cleaned_data["email"],
                "phone": form.cleaned_data.get("phone", ""),
                "admin_name": form.cleaned_data["admin_name"],
                "description": form.cleaned_data.get("description", ""),
            }
            request.session["club_registration_step1"] = session_data
            return redirect("clubs:register_step2")
    else:
        form = ClubRegistrationStep1Form(
            initial=request.session.get("club_registration_step1")
        )

    return render(request, "clubs/register_step1.html", {"form": form, "step": 1})


@login_required
@require_http_methods(["GET", "POST"])
def register_step2(request: HttpRequest) -> HttpResponse:
    """Шаг 2 — выбор тарифа."""
    if "club_registration_step1" not in request.session:
        messages.warning(request, "Сначала заполните данные клуба.")
        return redirect("clubs:register_step1")

    if request.method == "POST":
        plan = request.POST.get("plan")
        period = request.POST.get("period", "yearly")
        if plan in (ClubPlan.START, ClubPlan.BASIC, ClubPlan.PRO):
            request.session["club_registration_plan"] = plan
            request.session["club_registration_period"] = period
            return redirect("clubs:register_step3")
        messages.error(request, "Выберите тариф.")

    prices_for_template = _get_plan_prices_for_template()
    platform_plans: Any = list(get_platform_plans())
    if not platform_plans:
        from types import SimpleNamespace

        defaults = [
            ("start", "Старт", 990, 9900, 14, False, False, 1, 20),
            ("basic", "Базовый", 1990, 19900, 0, True, False, 5, 100),
            ("pro", "Про", 4990, 49900, 0, True, True, None, None),
        ]
        platform_plans = [
            SimpleNamespace(
                slug=slug,
                name=name,
                price_monthly=Decimal(str(price_monthly)),
                price_yearly=Decimal(str(price_yearly)),
                trial_days=trial_days,
                is_public_page=is_public_page,
                is_open_interclub=is_open_interclub,
                max_tournaments_per_month=max_tournaments_per_month,
                max_members=max_members,
            )
            for (
                slug,
                name,
                price_monthly,
                price_yearly,
                trial_days,
                is_public_page,
                is_open_interclub,
                max_tournaments_per_month,
                max_members,
            ) in defaults
        ]
    return render(
        request,
        "clubs/register_step2.html",
        {
            "step": 2,
            "plans": ClubPlan,
            "prices": prices_for_template,
            "platform_plans": platform_plans,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def register_step3(request: HttpRequest) -> HttpResponse:
    """Шаг 3 — активация клуба с trial-периодом."""
    step1 = request.session.get("club_registration_step1")
    plan = request.session.get("club_registration_plan", ClubPlan.START)
    period = request.session.get("club_registration_period", "yearly")
    if not step1:
        messages.warning(request, "Сначала заполните данные клуба.")
        return redirect("clubs:register_step1")

    if request.method == "POST":
        accept_rules = (
            str(request.POST.get("accept_organizer_rules") or "").strip().lower()
        )
        if accept_rules not in {"1", "true", "on", "yes"}:
            messages.error(
                request,
                "Для создания клуба необходимо принять правила для организаторов клубов.",
            )
            return redirect("clubs:register_step3")
        data = {**step1, "plan": ClubPlan.START, "period": period}
        try:
            club = create_club_with_trial(data, request.user)
        except ValueError as error:
            messages.error(request, str(error))
            return redirect("clubs:register_step1")
        record_platform_consent(
            request,
            "club_organizer_rules",
            get_legal_document_version("club-organizer-rules"),
        )
        for key in (
            "club_registration_step1",
            "club_registration_plan",
            "club_registration_period",
        ):
            request.session.pop(key, None)
        messages.success(
            request,
            f"Клуб «{club.name}» создан. Активирован бесплатный пробный период по тарифу Старт.",
        )
        messages.info(
            request,
            "Оплатить выбранный тариф можно в разделе «Подписка» в панели клуба.",
        )
        return redirect("clubs:dashboard", slug=club.slug)

    return render(
        request,
        "clubs/register_step3.html",
        {
            "step": 3,
            "step1": step1,
            "plan": plan,
            "period": period,
            "plan_label": dict(ClubPlan.choices).get(plan, plan),
        },
    )


@require_GET
def club_public_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Публичная страница клуба. 404 если страница скрыта или клуб приостановлен."""
    club = get_object_or_404(Club, slug=slug)
    can_manage = user_can_manage_club(request.user, club)

    if not club_has_public_page_access(club):
        if can_manage:
            if not club_is_operational(club):
                return redirect("clubs:subscription", slug=slug)
            if not club.is_public:
                return redirect("clubs:club_edit", slug=slug)

        if not club_is_operational(club):
            reason = "suspended"
        elif not club.is_public:
            reason = "hidden"
        else:
            reason = ""
        return render(
            request,
            "clubs/club_404.html",
            {"club": club, "reason": reason},
            status=404,
        )

    upcoming = list(
        Tournament.objects.filter(
            club=club,
            status__in=[
                TournamentStatus.UPCOMING,
                TournamentStatus.ACTIVE,
                TournamentStatus.GROUP_STAGE,
                TournamentStatus.PLAYOFFS,
            ],
        ).order_by("start_date")[:10]
    )
    for tournament in upcoming:
        _annotate_public_tournament_badge(tournament)
        tournament.public_action_label = (
            "Записаться"
            if tournament.public_badge_status == "upcoming" and not tournament.is_full()
            else "Подробнее"
        )
    recent_matches_qs = (
        Match.objects.filter(
            tournament__club=club,
            status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER],
            completed_datetime__gte=timezone.now() - timedelta(days=60),
        )
        .select_related(
            "tournament",
            "player1__user",
            "player2__user",
            "team1__player1__user",
            "team2__player1__user",
        )
        .order_by("-completed_datetime", "-pk")[:5]
    )

    recent_club_matches: list[dict[str, Any]] = []
    for match in recent_matches_qs:
        side1_player = match.get_side1_player()
        side2_player = match.get_side2_player()
        if not side1_player or not side2_player:
            continue
        if getattr(side1_player, "is_bye", False) or getattr(
            side2_player, "is_bye", False
        ):
            continue
        recent_club_matches.append(
            {
                "id": match.pk,
                "match_url": reverse("match_detail", kwargs={"pk": match.pk}),
                "tournament_name": match.tournament.name,
                "date": (match.completed_datetime or match.scheduled_datetime),
                "player1": match.get_player1_display(),
                "player2": match.get_player2_display(),
                "score": match.score_display,
                "p1_avatar": side1_player.avatar.url if side1_player.avatar else None,
                "p2_avatar": side2_player.avatar.url if side2_player.avatar else None,
            }
        )

    members_count = club.members.filter(status=ClubMemberStatus.ACTIVE).count()
    active_tournaments_count = club.tournaments.filter(
        status__in=[
            TournamentStatus.UPCOMING,
            TournamentStatus.ACTIVE,
            TournamentStatus.GROUP_STAGE,
            TournamentStatus.PLAYOFFS,
        ]
    ).count()
    foundation_year = club.created_at.year if club.created_at else None

    is_member = False
    is_pending_invite = False
    is_pending_join_request = False
    member_role: str | None = None
    if request.user.is_authenticated:
        membership = club.members.filter(user=request.user).first()
        if membership:
            is_member = membership.status == ClubMemberStatus.ACTIVE
            is_pending_invite = membership.status == ClubMemberStatus.INVITED
            if is_member:
                member_role = membership.role
        is_pending_join_request = ClubJoinRequest.objects.filter(
            club=club,
            user=request.user,
            status=ClubJoinRequestStatus.PENDING,
        ).exists()

    join_url = reverse("clubs:join", kwargs={"slug": club.slug})
    if not request.user.is_authenticated:
        login_url = reverse("login")
        join_url = (
            f"{login_url}?next={request.build_absolute_uri(request.get_full_path())}"
        )

    cta_label = "Вступить в клуб"
    cta_url = join_url
    cta_variant = "join"

    if request.user.is_authenticated and is_member and member_role:
        if member_role in (ClubMemberRole.ADMIN, ClubMemberRole.MANAGER):
            cta_label = "Панель управления"
            cta_url = reverse("clubs:dashboard", kwargs={"slug": club.slug})
            cta_variant = "manage"
        else:
            next_url = reverse("clubs:my_dashboard")
            set_current_url = reverse(
                "clubs:set_current_club", kwargs={"slug": club.slug}
            )
            cta_label = "Личный кабинет клуба"
            cta_url = f"{set_current_url}?next={next_url}"
            cta_variant = "player"
    elif request.user.is_authenticated and not is_pending_invite:
        if is_pending_join_request:
            cta_label = "Заявка отправлена"
            cta_url = ""
            cta_variant = "requested"
        else:
            cta_label = "Подать заявку в клуб"
            cta_url = reverse("clubs:join_request_create", kwargs={"slug": club.slug})
            cta_variant = "request"

    return render(
        request,
        "clubs/club_public_detail.html",
        {
            "club": club,
            "upcoming_tournaments": upcoming,
            "recent_club_matches": recent_club_matches,
            "is_member": is_member,
            "is_pending_invite": is_pending_invite,
            "is_pending_join_request": is_pending_join_request,
            "join_url": join_url,
            "members_count": members_count,
            "active_tournaments_count": active_tournaments_count,
            "foundation_year": foundation_year,
            "cta_label": cta_label,
            "cta_url": cta_url,
            "cta_variant": cta_variant,
            "is_club_panel": is_member,
            "can_manage_club": can_manage,
            "hide_club_header": True,
        },
    )


@require_http_methods(["GET", "POST"])
def club_join(request: HttpRequest, slug: str) -> HttpResponse:
    """Вступление в клуб по инвайт-токену."""
    club = get_object_or_404(Club, slug=slug)
    if not club_is_operational(club):
        return render(
            request,
            "clubs/join_error.html",
            {
                "club": club,
                "error": "club_suspended",
                "message": "Клуб приостановлен. Вступление временно недоступно.",
            },
        )
    token_value = request.GET.get("token") or (
        request.POST.get("token") if request.method == "POST" else None
    )

    if not token_value:
        return render(
            request,
            "clubs/join_error.html",
            {"club": club, "error": "invite_required"},
        )

    link = ClubInviteLink.objects.filter(
        club=club,
        token=token_value,
        is_active=True,
    ).first()
    if not link:
        return render(
            request,
            "clubs/join_error.html",
            {"club": club, "error": "invalid_token"},
        )
    if link.expires_at and timezone.now() > link.expires_at:
        return render(
            request,
            "clubs/join_error.html",
            {"club": club, "error": "expired"},
        )
    if link.max_uses is not None and link.use_count >= link.max_uses:
        return render(
            request,
            "clubs/join_error.html",
            {"club": club, "error": "limit_reached"},
        )

    if not request.user.is_authenticated:
        login_url = reverse("login")
        next_url = request.build_absolute_uri(request.get_full_path())
        return redirect(f"{login_url}?next={next_url}")

    existing = club.members.filter(user=request.user).first()
    if existing and existing.status == ClubMemberStatus.ACTIVE:
        messages.info(request, "Вы уже в этом клубе.")
        return redirect("clubs:club_public_detail", slug=slug)

    active_plans = ClubPlayerPlan.objects.filter(club=club, is_active=True).order_by(
        "sort_order",
        "name",
    )
    use_player_plans = club.use_player_plans and active_plans.exists()
    plan_form = ClubMemberPlanSelectForm(
        request.POST or None,
        club=club if use_player_plans else None,
    )

    if request.method == "POST":
        selected_plan = None
        if use_player_plans:
            if not plan_form.is_valid():
                return render(
                    request,
                    "clubs/join_confirm.html",
                    {
                        "club": club,
                        "token": token_value,
                        "plan_form": plan_form,
                        "has_plans": True,
                    },
                )
            selected_plan = plan_form.cleaned_data["plan_id"]

        if existing and existing.status == ClubMemberStatus.INVITED:
            existing.status = ClubMemberStatus.ACTIVE
            existing.joined_at = timezone.now()
            existing.save(update_fields=["status", "joined_at"])
            member = existing
            ClubRating.objects.get_or_create(
                club=club, member=member, defaults={"points": 0}
            )
        else:
            can_add, limit_msg = club_can_add_member(club)
            if not can_add:
                return render(
                    request,
                    "clubs/join_error.html",
                    {"club": club, "error": "member_limit", "message": limit_msg},
                )
            member = ClubMember.objects.create(
                club=club,
                user=request.user,
                role=ClubMemberRole.PLAYER,
                status=ClubMemberStatus.ACTIVE,
                invited_by=link.created_by,
                joined_at=timezone.now(),
            )
            ClubRating.objects.create(club=club, member=member, points=0)

        if selected_plan is not None:
            assign_member_plan(
                member,
                selected_plan,
                assigned_by=link.created_by,
                change_reason="Выбор тарифа при вступлении",
            )

        link.use_count += 1
        link.save(update_fields=["use_count"])

        try:
            dashboard_url = request.build_absolute_uri(
                reverse("clubs:dashboard", kwargs={"slug": club.slug})
            )
            send_new_member_notification(club, member, dashboard_url=dashboard_url)
        except Exception:
            logger.exception("Ошибка отправки уведомления о новом участнике")

        messages.success(request, f"Вы вступили в клуб «{club.name}».")
        return redirect("clubs:club_public_detail", slug=slug)

    return render(
        request,
        "clubs/join_confirm.html",
        {
            "club": club,
            "token": token_value,
            "plan_form": plan_form,
            "has_plans": use_player_plans,
        },
    )


@login_required
@require_POST
def join_request_create(request: HttpRequest, slug: str) -> HttpResponse:
    """Подать заявку на вступление в клуб без инвайт-ссылки."""
    club = get_object_or_404(Club, slug=slug)
    fallback_url = reverse("clubs:club_public_detail", kwargs={"slug": slug})
    redirect_url = _safe_next_url(request, fallback_url)
    if not club_is_operational(club):
        messages.error(request, "Клуб временно недоступен для вступления.")
        return redirect(redirect_url)

    existing_member = club.members.filter(user=request.user).first()
    if existing_member:
        if existing_member.status == ClubMemberStatus.ACTIVE:
            messages.info(request, "Вы уже состоите в этом клубе.")
        elif existing_member.status == ClubMemberStatus.INVITED:
            messages.info(
                request,
                "У вас уже есть приглашение в этот клуб. Примите его в разделе приглашений.",
            )
        else:
            messages.info(request, "Ваш статус в этом клубе уже существует.")
        return redirect(redirect_url)

    can_add, limit_msg = club_can_add_member(club)
    if not can_add:
        messages.error(request, limit_msg)
        return redirect(redirect_url)

    join_request, created = ClubJoinRequest.objects.get_or_create(
        club=club,
        user=request.user,
        defaults={
            "status": ClubJoinRequestStatus.PENDING,
            "message": (request.POST.get("message") or "").strip(),
        },
    )
    if not created:
        if join_request.status == ClubJoinRequestStatus.PENDING:
            messages.info(request, "Заявка в этот клуб уже отправлена.")
            return redirect(redirect_url)
        join_request.status = ClubJoinRequestStatus.PENDING
        join_request.message = (request.POST.get("message") or "").strip()
        join_request.reviewed_by = None
        join_request.reviewed_at = None
        join_request.save(
            update_fields=[
                "status",
                "message",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )

    for admin_member in club.members.filter(
        role=ClubMemberRole.ADMIN,
        status=ClubMemberStatus.ACTIVE,
    ).select_related("user"):
        Notification.objects.create(
            user=admin_member.user,
            message=f"Новая заявка на вступление в клуб «{club.name}» от {request.user}.",
            url=reverse("clubs:invites_list", kwargs={"slug": club.slug}),
        )

    messages.success(
        request,
        "Заявка на вступление отправлена. Дождитесь решения администратора клуба.",
    )
    return redirect(redirect_url)


@login_required
@require_http_methods(["GET", "POST"])
def set_current_club(request: HttpRequest, slug: str) -> HttpResponse:
    """Установить текущий клуб в сессии (переключатель клубов)."""
    member = (
        ClubMember.objects.filter(
            user=request.user,
            club__slug=slug,
            status=ClubMemberStatus.ACTIVE,
        )
        .select_related("club")
        .first()
    )
    if not member:
        messages.error(request, "Клуб не найден или вы не являетесь участником.")
        return redirect("clubs:register_choice")
    request.session["current_club_slug"] = slug
    next_url = request.GET.get("next") or reverse("clubs:my_dashboard")
    return redirect(next_url)
