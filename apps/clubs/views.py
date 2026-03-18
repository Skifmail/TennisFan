"""
Views клубного раздела: регистрация клуба, публичная страница, инвайты, панель управления.
"""

import csv
import logging
import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal
from io import StringIO
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.uploadedfile import UploadedFile
from django.core.paginator import Paginator
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.payments.yookassa_client import (
    create_payment,
    create_payment_with_credentials,
    get_payment_status_with_credentials,
)
from apps.tournaments.models import (
    Match,
    Tournament,
    TournamentDuration,
    TournamentStatus,
)

from .forms import (
    ClubInviteLinkForm,
    ClubMemberPlanAssignForm,
    ClubMemberPlanSelectForm,
    ClubMembershipFeeSettingsForm,
    ClubNotificationConfigForm,
    ClubNotificationSettingsForm,
    ClubPlayerPlanForm,
    ClubProfileEditForm,
    ClubRegistrationStep1Form,
    ClubTournamentCreateForm,
    InviteByEmailForm,
    MarkFeePaidForm,
)
from .models import (
    Club,
    ClubApplicationStatus,
    ClubFeePayment,
    ClubFeePaymentPending,
    ClubInviteLink,
    ClubMember,
    ClubMemberRole,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubNotificationConfig,
    ClubNotificationSettings,
    ClubPlan,
    ClubPlanTournamentAccess,
    ClubPlayerPlan,
    ClubRating,
    ClubRatingHistory,
    ClubSubscription,
    ClubSubscriptionPaymentPending,
    ClubSubscriptionPeriod,
    ClubSubscriptionStatus,
    ClubTournamentApplication,
    FeePaymentMethod,
)
from .notifications import (
    send_club_invite_email,
    send_fee_paid_notification,
    send_new_member_notification,
)
from .payment_utils import decrypt_secret, encrypt_secret
from .plan_services import (
    assign_member_plan,
    get_member_active_plan,
    get_member_plan_limits,
)
from .services import (
    club_can_add_member,
    club_can_create_tournament_this_month,
    club_has_public_page_access,
    club_is_operational,
    create_club_with_trial,
    get_club_current_subscription,
    get_current_period_label,
    get_fee_status_for_member,
    get_platform_plan,
    get_platform_plans,
    user_can_edit_club_settings,
    user_can_manage_club,
    user_can_manage_fees,
    user_can_manage_managers,
)

logger = logging.getLogger(__name__)


def _get_plan_prices_for_template() -> dict:
    """Словарь цен по тарифам для register_step2: {START: {monthly, yearly, description}, ...}."""
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
    for p in get_platform_plans():
        key = p.slug.upper()
        result[key] = {
            "monthly": float(p.price_monthly),
            "yearly": float(p.price_yearly),
            "description": p.description
            or defaults.get(key, {}).get("description", ""),
        }
    return result


def _get_plan_prices_for_subscription() -> dict:
    """Словарь цен для subscription_pay: {ClubPlan.START: {monthly, yearly}, ...}."""
    result = {}
    for plan_slug in ("start", "basic", "pro"):
        pp = get_platform_plan(plan_slug)
        if pp:
            result[plan_slug] = {
                "monthly": pp.price_monthly,
                "yearly": pp.price_yearly,
            }
        else:
            result[plan_slug] = {
                "monthly": Decimal("0"),
                "yearly": Decimal("0"),
            }
    return result


# ---------------------------------------------------------------------------
# Выбор типа аккаунта и регистрация клуба (3 шага)
# ---------------------------------------------------------------------------


@require_GET
def register_choice(request: HttpRequest) -> HttpResponse:
    """Страница выбора: регистрация как игрок или как клуб."""
    return render(request, "clubs/register_choice.html")


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
        # Fallback: миграция не применена или таблица пуста
        from types import SimpleNamespace

        defaults = [
            ("start", "Старт", 990, 9900, 14, False, False, 1, 20),
            ("basic", "Базовый", 1990, 19900, 0, True, False, 5, 100),
            ("pro", "Про", 4990, 49900, 0, True, True, None, None),
        ]
        platform_plans = [
            SimpleNamespace(
                slug=s,
                name=n,
                price_monthly=Decimal(str(pm)),
                price_yearly=Decimal(str(py)),
                trial_days=td,
                is_public_page=ipp,
                is_open_interclub=ioc,
                max_tournaments_per_month=mt,
                max_members=mm,
            )
            for s, n, pm, py, td, ipp, ioc, mt, mm in defaults
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
        # На этапе регистрации всегда создаём клуб с бесплатным trial по тарифу Старт.
        # После создания ведём в панель клуба; оплату выбранного тарифа админ может
        # оформить позже в разделе «Подписка».
        data = {**step1, "plan": ClubPlan.START, "period": period}
        try:
            club = create_club_with_trial(data, request.user)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("clubs:register_step1")
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


# ---------------------------------------------------------------------------
# Публичная страница и дашборд-заглушка
# ---------------------------------------------------------------------------


@require_GET
def club_public_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Публичная страница клуба. 404 если страница скрыта или клуб приостановлен."""
    club = get_object_or_404(Club, slug=slug)
    can_manage = user_can_manage_club(request.user, club)

    if not club_has_public_page_access(club):
        # Для админа/менеджера не показываем 404, а ведём в нужный раздел.
        if can_manage:
            if not club_is_operational(club):
                return redirect("clubs:subscription", slug=slug)
            if not club.is_public:
                return redirect("clubs:club_edit", slug=slug)

        reason: str
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

    upcoming = Tournament.objects.filter(
        club=club, status=TournamentStatus.UPCOMING
    ).order_by("start_date")[:10]
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
    member_role: str | None = None
    if request.user.is_authenticated:
        membership = club.members.filter(user=request.user).first()
        if membership:
            is_member = membership.status == ClubMemberStatus.ACTIVE
            is_pending_invite = membership.status == ClubMemberStatus.INVITED
            if is_member:
                member_role = membership.role

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
            cta_label = "Панель управления →"
            cta_url = reverse("clubs:dashboard", kwargs={"slug": club.slug})
            cta_variant = "manage"
        else:
            # Для игрока ведём в личный кабинет текущего клуба.
            next_url = reverse("clubs:my_dashboard")
            set_current_url = reverse(
                "clubs:set_current_club", kwargs={"slug": club.slug}
            )
            cta_label = "Личный кабинет клуба →"
            cta_url = f"{set_current_url}?next={next_url}"
            cta_variant = "player"

    return render(
        request,
        "clubs/club_public_detail.html",
        {
            "club": club,
            "upcoming_tournaments": upcoming,
            "recent_club_matches": recent_club_matches,
            "is_member": is_member,
            "is_pending_invite": is_pending_invite,
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


@login_required
@require_GET
def dashboard(request: HttpRequest, slug: str) -> HttpResponse:
    """Дашборд клуба: статистика и навигация по разделам панели."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_club(request.user, club):
        messages.error(request, "У вас нет доступа к управлению этим клубом.")
        return redirect("clubs:club_public_detail", slug=slug)
    if not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return redirect("clubs:club_public_detail", slug=slug)
    subscription = get_club_current_subscription(club)
    # Лимиты по тарифу платформы (участники и турниры)
    plan_slug: str = subscription.plan if subscription else "start"
    platform_plan = get_platform_plan(plan_slug)
    members_limit = platform_plan.max_members if platform_plan else None
    tournaments_limit = (
        platform_plan.max_tournaments_per_month if platform_plan else None
    )
    members_count = club.members.filter(status=ClubMemberStatus.ACTIVE).count()
    tournaments_count = club.tournaments.count()
    fee_active = ClubMembershipFee.objects.filter(club=club, is_active=True).exists()
    plans_count = ClubPlayerPlan.objects.filter(club=club, is_active=True).count()
    recent_tournaments = club.tournaments.order_by("-start_date")[:5]
    recent_members = (
        club.members.filter(status=ClubMemberStatus.ACTIVE)
        .select_related("user")
        .order_by("-joined_at")[:5]
    )
    return render(
        request,
        "clubs/dashboard.html",
        {
            "is_club_panel": True,
            "club": club,
            "members_count": members_count,
            "tournaments_count": tournaments_count,
            "members_limit": members_limit,
            "tournaments_limit": tournaments_limit,
            "subscription": subscription,
            "fee_active": fee_active,
            "plans_count": plans_count,
            "recent_tournaments": recent_tournaments,
            "recent_members": recent_members,
            "can_edit_settings": user_can_edit_club_settings(request.user, club),
            "can_manage_fees": user_can_manage_fees(request.user, club),
            "can_manage_managers": user_can_manage_managers(request.user, club),
        },
    )


@login_required
@require_GET
def plans_manage(request: HttpRequest, slug: str) -> HttpResponse:
    """Показывает список клубных тарифов и форму назначения участнику.

    Args:
        request: HTTP-запрос пользователя.
        slug: Slug клуба.

    Returns:
        HttpResponse: Страница управления тарифами.
    """
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
    plans = ClubPlayerPlan.objects.filter(club=club).order_by("sort_order", "name")
    assign_form = ClubMemberPlanAssignForm(club=club)
    return render(
        request,
        "clubs/plans_manage.html",
        {
            "is_club_panel": True,
            "club": club,
            "plans": plans,
            "assign_form": assign_form,
            "can_edit_settings": user_can_edit_club_settings(request.user, club),
            "can_manage_fees": user_can_manage_fees(request.user, club),
            "can_manage_managers": user_can_manage_managers(request.user, club),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def plan_create(request: HttpRequest, slug: str) -> HttpResponse:
    """Создаёт новый тариф игроков для клуба.

    Args:
        request: HTTP-запрос пользователя.
        slug: Slug клуба.

    Returns:
        HttpResponse: Форма создания или редирект в список тарифов.
    """
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir

    if request.method == "POST":
        form = ClubPlayerPlanForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.club = club
            obj.save()
            messages.success(request, f"Тариф «{obj.name}» создан.")
            return redirect("clubs:plans_manage", slug=slug)
    else:
        form = ClubPlayerPlanForm()

    return render(
        request,
        "clubs/plan_form.html",
        {"club": club, "form": form, "is_edit": False, "is_club_panel": True},
    )


@login_required
@require_http_methods(["GET", "POST"])
def plan_edit(request: HttpRequest, slug: str, plan_id: int) -> HttpResponse:
    """Редактирует существующий тариф клуба.

    Args:
        request: HTTP-запрос пользователя.
        slug: Slug клуба.
        plan_id: Идентификатор тарифа.

    Returns:
        HttpResponse: Форма редактирования или редирект в список тарифов.
    """
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
    plan = get_object_or_404(ClubPlayerPlan, club=club, id=plan_id)

    if request.method == "POST":
        form = ClubPlayerPlanForm(request.POST, instance=plan)
        if form.is_valid():
            form.save()
            messages.success(request, f"Тариф «{plan.name}» обновлен.")
            return redirect("clubs:plans_manage", slug=slug)
    else:
        form = ClubPlayerPlanForm(instance=plan)

    return render(
        request,
        "clubs/plan_form.html",
        {
            "club": club,
            "form": form,
            "is_edit": True,
            "plan": plan,
            "is_club_panel": True,
        },
    )


@login_required
@require_POST
def plan_assign_member(request: HttpRequest, slug: str) -> HttpResponse:
    """Назначает тариф выбранному участнику клуба.

    Args:
        request: HTTP-запрос пользователя.
        slug: Slug клуба.

    Returns:
        HttpResponse: Редирект на страницу управления тарифами.
    """
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir

    form = ClubMemberPlanAssignForm(request.POST, club=club)
    if not form.is_valid():
        for errs in form.errors.values():
            messages.error(request, "; ".join(errs))
        return redirect("clubs:plans_manage", slug=slug)

    member: ClubMember = form.cleaned_data["member"]
    plan: ClubPlayerPlan = form.cleaned_data["plan"]
    reason = (form.cleaned_data.get("reason") or "").strip()
    assign_member_plan(
        member,
        plan,
        assigned_by=request.user,
        change_reason=reason,
    )
    messages.success(
        request,
        f"Участнику {member.user.email} назначен тариф «{plan.name}».",
    )
    return redirect("clubs:plans_manage", slug=slug)


# ---------------------------------------------------------------------------
# Инвайт-ссылки и вступление по токену
# ---------------------------------------------------------------------------


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
            request, "clubs/join_error.html", {"club": club, "error": "no_token"}
        )

    link = ClubInviteLink.objects.filter(
        club=club, token=token_value, is_active=True
    ).first()
    if not link:
        return render(
            request, "clubs/join_error.html", {"club": club, "error": "invalid_token"}
        )
    if link.expires_at and timezone.now() > link.expires_at:
        return render(
            request, "clubs/join_error.html", {"club": club, "error": "expired"}
        )
    if link.max_uses is not None and link.use_count >= link.max_uses:
        return render(
            request, "clubs/join_error.html", {"club": club, "error": "limit_reached"}
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
    plan_form = ClubMemberPlanSelectForm(
        request.POST or None,
        club=club if active_plans.exists() else None,
    )

    if request.method == "POST":
        selected_plan: ClubPlayerPlan | None = None
        if active_plans.exists():
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
            from .models import ClubRating

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
            from .models import ClubRating

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
            "has_plans": active_plans.exists(),
        },
    )


# ---------------------------------------------------------------------------
# Входящие приглашения (ЛК игрока): принять / отклонить
# ---------------------------------------------------------------------------


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
    from .models import ClubRating

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


# ---------------------------------------------------------------------------
# Добавление игрока админом: поиск по email, приглашение, импорт CSV
# ---------------------------------------------------------------------------


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
                # Если участник был исключён, реактивируем его и отправляем новое приглашение.
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
        not_found = []
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
            msg += f" Не найдено на платформе: {len(not_found)} ({', '.join(not_found[:5])}{'…' if len(not_found) > 5 else ''})."
        messages.success(request, msg)
        return redirect("clubs:dashboard", slug=slug)

    return render(
        request,
        "clubs/invite_import_csv.html",
        {"club": club, "is_club_panel": True},
    )


# ---------------------------------------------------------------------------
# Редактирование профиля клуба (админ/менеджер)
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def club_edit(request: HttpRequest, slug: str) -> HttpResponse:
    """Редактирование профиля клуба (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_edit_club_settings(request.user, club):
        messages.error(
            request, "Редактировать настройки клуба может только администратор."
        )
        return redirect("clubs:dashboard", slug=slug)

    if request.method == "POST":
        form = ClubProfileEditForm(request.POST, request.FILES, instance=club)
        if form.is_valid():
            form.save()
            messages.success(request, "Профиль клуба сохранён.")
            return redirect("clubs:club_public_detail", slug=slug)
    else:
        form = ClubProfileEditForm(instance=club)

    return render(
        request,
        "clubs/club_edit.html",
        {"club": club, "form": form, "is_club_panel": True},
    )


# ---------------------------------------------------------------------------
# ЛК игрока клуба: текущий клуб из сессии, разделы «Мои турниры», «Рейтинг», «Взнос»
# ---------------------------------------------------------------------------


def _get_current_club_member(request: HttpRequest) -> ClubMember | None:
    """Возвращает ClubMember для текущего клуба пользователя из сессии или None."""
    if not request.user.is_authenticated:
        return None
    slug = request.session.get("current_club_slug")
    if not slug:
        first = (
            ClubMember.objects.filter(
                user=request.user,
                status=ClubMemberStatus.ACTIVE,
            )
            .select_related("club")
            .order_by("club__name")
            .first()
        )
        member = cast(ClubMember | None, first)
        if member:
            request.session["current_club_slug"] = member.club.slug
        return member
    existing = (
        ClubMember.objects.filter(
            user=request.user,
            club__slug=slug,
            status=ClubMemberStatus.ACTIVE,
        )
        .select_related("club")
        .first()
    )
    return cast(ClubMember | None, existing)


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


def _build_club_profile_context(
    request: HttpRequest,
    *,
    club: Club,
    member: ClubMember,
    player: Any,
    is_profile_owner: bool,
) -> dict[str, Any]:
    """Собирает контекст клубного кабинета игрока."""
    from apps.tournaments.models import Match
    from apps.users.rating_utils import rating_to_ntrp_level

    fee = (
        ClubMembershipFee.objects.filter(club=club, is_active=True)
        .order_by("-id")
        .first()
    )
    fee_status = get_fee_status_for_member(club, member) if fee else None
    member_plan = get_member_active_plan(member)
    plan_limits = get_member_plan_limits(member)

    all_matches_qs = (
        Match.objects.filter(
            tournament__club=club,
        )
        .filter(
            Q(player1=player)
            | Q(player2=player)
            | Q(team1__player1=player)
            | Q(team1__player2=player)
            | Q(team2__player1=player)
            | Q(team2__player2=player)
        )
        .select_related(
            "tournament",
            "player1",
            "player2",
            "winner",
            "team1",
            "team2",
            "winner_team",
        )
        .annotate(
            effective_date=Coalesce(
                "scheduled_datetime", "deadline", "completed_datetime"
            ),
        )
        .order_by("-effective_date")
        .distinct()
    )

    filter_year = request.GET.get("year")
    filter_month = request.GET.get("month")
    filter_status = request.GET.get("status")

    from django.db.models import Max, Min

    date_range = all_matches_qs.aggregate(
        min_date=Min("effective_date"),
        max_date=Max("effective_date"),
    )
    min_date = date_range["min_date"]
    max_date = date_range["max_date"]

    available_years: list[int] = []
    if min_date and max_date:
        available_years = list(range(max_date.year, min_date.year - 1, -1))

    active_year: int | None = None
    active_month: int | None = None
    active_status: str | None = None

    if filter_year:
        try:
            active_year = int(filter_year)
        except (ValueError, TypeError):
            active_year = None

    if filter_month:
        try:
            active_month = int(filter_month)
            if not (1 <= active_month <= 12):
                active_month = None
        except (ValueError, TypeError):
            active_month = None

    if filter_status:
        valid_statuses = [s[0] for s in Match.MatchStatus.choices]
        if filter_status in valid_statuses:
            active_status = filter_status

    if active_year:
        all_matches_qs = all_matches_qs.filter(effective_date__year=active_year)
    if active_month and active_year:
        all_matches_qs = all_matches_qs.filter(effective_date__month=active_month)
    if active_status:
        all_matches_qs = all_matches_qs.filter(status=active_status)

    recent_matches = list(all_matches_qs)

    months_ru = [
        (1, "Январь"),
        (2, "Февраль"),
        (3, "Март"),
        (4, "Апрель"),
        (5, "Май"),
        (6, "Июнь"),
        (7, "Июль"),
        (8, "Август"),
        (9, "Сентябрь"),
        (10, "Октябрь"),
        (11, "Ноябрь"),
        (12, "Декабрь"),
    ]

    def _build_club_profile_progress_data() -> list[dict[str, Any]]:
        completed_qs = (
            Match.objects.filter(
                tournament__club=club,
            )
            .filter(
                Q(player1=player)
                | Q(player2=player)
                | Q(team1__player1=player)
                | Q(team1__player2=player)
                | Q(team2__player1=player)
                | Q(team2__player2=player)
            )
            .filter(
                status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]
            )
            .select_related("winner", "winner_team", "team1", "team2")
            .order_by("completed_datetime", "scheduled_datetime", "pk")
            .distinct()
        )

        start = player.created_at.date() if player.created_at else timezone.now().date()
        result: list[dict[str, Any]] = [
            {
                "date": start.isoformat(),
                "points": 0.0,
                "matches": 0,
                "win_rate": 0.0,
                "won": None,
                "fan_delta": 0.0,
                "ntrp_before": 0.0,
                "ntrp_after": 0.0,
            }
        ]

        rating_points = 0.0
        cum_matches = 0
        cum_wins = 0

        for match in completed_qs:
            event_date = (
                (match.completed_datetime and match.completed_datetime.date())
                or (match.scheduled_datetime and match.scheduled_datetime.date())
                or timezone.now().date()
            )
            if match.team1_id and match.team2_id:
                on_team1 = match.team1 and (
                    match.team1.player1_id == player.pk
                    or match.team1.player2_id == player.pk
                )
                fan_delta = float(
                    match.rating_delta_player1
                    if on_team1
                    else match.rating_delta_player2
                )
            else:
                fan_delta = float(
                    match.rating_delta_player1
                    if match.player1_id == player.pk
                    else match.rating_delta_player2
                )

            won = bool(
                (match.winner_id == player.pk)
                or (
                    match.winner_team_id
                    and match.team1_id
                    and match.winner_team_id == match.team1_id
                    and (
                        match.team1.player1_id == player.pk
                        or match.team1.player2_id == player.pk
                    )
                )
                or (
                    match.winner_team_id
                    and match.team2_id
                    and match.winner_team_id == match.team2_id
                    and (
                        match.team2.player1_id == player.pk
                        or match.team2.player2_id == player.pk
                    )
                )
            )

            rating_before = rating_points
            rating_points += fan_delta
            ntrp_before_val = rating_to_ntrp_level(rating_before)
            ntrp_after_val = rating_to_ntrp_level(rating_points)
            ntrp_before = float(ntrp_before_val) if ntrp_before_val else 0.0
            ntrp_after = float(ntrp_after_val) if ntrp_after_val else 0.0

            cum_matches += 1
            if won:
                cum_wins += 1
            win_rate = round(cum_wins / cum_matches * 100, 1) if cum_matches else 0.0

            on_team1 = (
                match.team1_id
                and (
                    match.team1.player1_id == player.pk
                    or match.team1.player2_id == player.pk
                )
            ) or (match.player1_id == player.pk)
            opponent = (
                match.get_player2_display() if on_team1 else match.get_player1_display()
            )

            result.append(
                {
                    "date": event_date.isoformat(),
                    "points": round(rating_points, 1),
                    "matches": cum_matches,
                    "win_rate": win_rate,
                    "won": won,
                    "fan_delta": fan_delta,
                    "ntrp_before": ntrp_before,
                    "ntrp_after": ntrp_after,
                    "match_id": match.pk,
                    "match_opponent": opponent,
                    "match_score": match.score_display,
                }
            )

        return result

    def _build_club_season_points_data(
        progress_data: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        points_series: list[dict[str, Any]] = []
        for item in progress_data:
            if item.get("matches", 0) <= 0:
                continue
            points_series.append(
                {
                    "date": item["date"],
                    "season_points": int(round(float(item.get("points", 0.0)))),
                }
            )
        if not points_series:
            today = timezone.now().date().isoformat()
            return [{"date": today, "season_points": 0}]
        return points_series

    progress_data = _build_club_profile_progress_data()
    season_points_data = _build_club_season_points_data(progress_data)
    season_points_current = (
        season_points_data[-1]["season_points"] if season_points_data else 0
    )

    from types import SimpleNamespace

    season_points = SimpleNamespace(current_season_points=season_points_current)
    current_season_display = f"Клуб {club.name}"

    matches_played = sum(1 for row in progress_data if row.get("matches", 0) > 0)
    wins = sum(
        1
        for row in progress_data
        if row.get("matches", 0) > 0 and row.get("won") is True
    )
    club_win_rate = round((wins / matches_played) * 100, 1) if matches_played else 0.0
    club_points_now = (
        float(progress_data[-1].get("points", 0.0)) if progress_data else 0.0
    )

    if (
        progress_data
        and progress_data[-1]["date"] != timezone.now().date().isoformat()
        and matches_played > 0
    ):
        progress_data.append(
            {
                "date": timezone.now().date().isoformat(),
                "points": club_points_now,
                "matches": matches_played,
                "win_rate": club_win_rate,
                "won": None,
                "fan_delta": 0.0,
                "ntrp_before": 0.0,
                "ntrp_after": float(rating_to_ntrp_level(club_points_now) or 0.0),
            }
        )

    subscription_usage_percent = 0
    if plan_limits and plan_limits.monthly_tournaments_limit:
        if plan_limits.monthly_tournaments_limit > 0:
            used = plan_limits.tournaments_used
            limit = plan_limits.monthly_tournaments_limit
            subscription_usage_percent = min(100, int((used / limit) * 100))

    from apps.player_ratings.services import get_player_skills

    player_skills_data = None
    try:
        player_skills_data = get_player_skills(
            player, request.user, include_lowest_three=True
        )
    except Exception:
        player_skills_data = None

    return {
        "club": club,
        "is_club_panel": True,
        "is_club_profile": True,
        "player": player,
        "member": member,
        "member_plan": member_plan,
        "plan_limits": plan_limits,
        "fee": fee,
        "fee_status": fee_status,
        "recent_matches": recent_matches,
        "profile_progress_data": progress_data,
        "subscription_usage_percent": subscription_usage_percent,
        "available_years": available_years,
        "months_ru": months_ru,
        "active_year": active_year,
        "active_month": active_month,
        "active_status": active_status,
        "match_statuses": Match.MatchStatus.choices,
        "season_points": season_points,
        "current_season_display": current_season_display,
        "season_points_data": season_points_data,
        "season_championships": [],
        "player_skills_data": player_skills_data,
        "is_profile_owner": is_profile_owner,
        "can_view_profile_stats": True,
        "subscription_autopay_card": None,
        "club_matches_played": matches_played,
        "club_win_rate": club_win_rate,
        "club_points_now": club_points_now,
    }


@login_required
@require_GET
def my_dashboard(request: HttpRequest) -> HttpResponse:
    """Отображает личный кабинет игрока внутри текущего клуба.

    Args:
        request (HttpRequest): HTTP-запрос пользователя.

    Returns:
        HttpResponse: Страница личного кабинета клуба.
    """
    member = _get_current_club_member(request)
    if not member:
        messages.info(
            request,
            "Вы не состоите в клубе. Вступите по приглашению или создайте клуб.",
        )
        return redirect("clubs:register_choice")

    club = member.club
    player = getattr(request.user, "player", None)
    if player is None:
        messages.error(request, "Профиль игрока не найден.")
        return redirect("clubs:register_choice")
    context = _build_club_profile_context(
        request,
        club=club,
        member=member,
        player=player,
        is_profile_owner=True,
    )
    return render(request, "users/profile.html", context)


@login_required
@require_GET
def member_detail(request: HttpRequest, slug: str, member_id: int) -> HttpResponse:
    """Клубный кабинет выбранного участника для управляющих клубом."""
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir

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
    return render(request, "users/profile.html", context)


@login_required
@require_GET
def my_tournaments(request: HttpRequest) -> HttpResponse:
    """Раздел «Мои турниры» — турниры текущего клуба, в которых участвует игрок.

    Показываются только те турниры, где текущий игрок (request.user.player)
    записан в участники конкретного клубного турнира.
    """
    member = _get_current_club_member(request)
    if not member:
        messages.info(
            request,
            "Вы не состоите в клубе. Вступите по приглашению или создайте клуб.",
        )
        return redirect("clubs:register_choice")

    club = member.club
    player = getattr(request.user, "player", None)
    status_filter = request.GET.get("status", "upcoming")

    # Если у пользователя нет связанного игрока, для "Мои турниры" ничего не показываем.
    qs = Tournament.objects.none()
    if player is not None:
        qs = (
            Tournament.objects.filter(club=club, participants=player)
            .order_by("start_date")
            .distinct()
        )
    if status_filter == "upcoming":
        qs = qs.filter(status="upcoming")
    elif status_filter == "active":
        qs = qs.filter(status__in=["active", "group_stage", "playoffs"])
    elif status_filter == "completed":
        qs = qs.filter(status="completed")

    # Предупреждение о взносе: если у клуба restrict_tournament_access и участник не оплатил
    fee = ClubMembershipFee.objects.filter(club=club, is_active=True).first()
    fee_restrict = (
        fee
        and fee.restrict_tournament_access
        and get_fee_status_for_member(club, member) == "unpaid"
    )
    member_plan = get_member_active_plan(member)
    plan_limits = get_member_plan_limits(member)

    return render(
        request,
        "clubs/my_tournaments.html",
        {
            "club": club,
            "is_club_panel": True,
            "tournaments": qs[:50],
            "status_filter": status_filter,
            "fee_restrict": fee_restrict,
            "member_plan": member_plan,
            "plan_limits": plan_limits,
        },
    )


@login_required
@require_GET
def my_plan(request: HttpRequest) -> HttpResponse:
    """Показывает текущий тариф игрока и остатки лимитов.

    Args:
        request: HTTP-запрос пользователя.

    Returns:
        HttpResponse: Страница "Мой тариф".
    """
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    club = member.club
    active_plans = ClubPlayerPlan.objects.filter(club=club, is_active=True).order_by(
        "sort_order",
        "name",
    )
    member_plan = get_member_active_plan(member)
    plan_limits = get_member_plan_limits(member)
    can_self_change = bool(member_plan and member_plan.plan.allow_self_change)
    return render(
        request,
        "clubs/my_plan.html",
        {
            "club": club,
            "is_club_panel": True,
            "member_plan": member_plan,
            "plan_limits": plan_limits,
            "active_plans": active_plans,
            "can_self_change": can_self_change,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def my_plan_change(request: HttpRequest) -> HttpResponse:
    """Позволяет участнику сменить тариф клуба (если разрешено).

    Args:
        request: HTTP-запрос пользователя.

    Returns:
        HttpResponse: Форма смены тарифа или редирект после сохранения.
    """
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    current_member_plan = get_member_active_plan(member)
    if current_member_plan and not current_member_plan.plan.allow_self_change:
        messages.error(request, "Клуб запретил самостоятельную смену тарифа.")
        return redirect("clubs:my_plan")

    form = ClubMemberPlanSelectForm(request.POST or None, club=member.club)
    if request.method == "POST" and form.is_valid():
        selected_plan: ClubPlayerPlan = form.cleaned_data["plan_id"]
        assign_member_plan(
            member,
            selected_plan,
            assigned_by=request.user,
            change_reason="Смена тарифа участником",
        )
        messages.success(request, f"Ваш тариф изменён на «{selected_plan.name}».")
        return redirect("clubs:my_plan")

    return render(
        request,
        "clubs/my_plan_change.html",
        {
            "club": member.club,
            "is_club_panel": True,
            "form": form,
            "current_member_plan": current_member_plan,
        },
    )


@login_required
@require_GET
def club_rating(request: HttpRequest) -> HttpResponse:
    """Раздел «Рейтинг клуба» — место, очки, таблица, история."""
    member = _get_current_club_member(request)
    if not member:
        return redirect("clubs:register_choice")

    club = member.club
    ratings = list(
        ClubRating.objects.filter(club=club)
        .select_related("member", "member__user")
        .order_by("-points")
    )
    # Место при отображении (rank может быть не заполнен в БД)
    for i, r in enumerate(ratings, 1):
        r.display_rank = i
    my_rating = next((r for r in ratings if r.member_id == member.id), None)
    history = []
    if my_rating:
        history = list(
            ClubRatingHistory.objects.filter(club_rating=my_rating)
            .select_related("tournament")
            .order_by("-created_at")[:20]
        )

    return render(
        request,
        "clubs/club_rating.html",
        {
            "club": club,
            "is_club_panel": True,
            "ratings": ratings,
            "my_rating": my_rating,
            "history": history,
        },
    )


@login_required
@require_GET
def my_fees(request: HttpRequest) -> HttpResponse:
    """Раздел «Членский взнос» — статус и история (оплата в Phase 5)."""
    member = _get_current_club_member(request)
    if not member:
        return redirect("clubs:register_choice")

    club = member.club
    fee = (
        ClubMembershipFee.objects.filter(club=club, is_active=True)
        .order_by("-id")
        .first()
    )
    fee_status = get_fee_status_for_member(club, member) if fee else None
    payments = []
    if member:
        payments = list(
            ClubFeePayment.objects.filter(member=member)
            .select_related("fee")
            .order_by("-paid_at")[:30]
        )

    return render(
        request,
        "clubs/my_fees.html",
        {
            "club": club,
            "is_club_panel": True,
            "fee": fee,
            "fee_status": fee_status,
            "payments": payments,
        },
    )


# ---------------------------------------------------------------------------
# Панель клуба: игроки, турниры, рейтинг, взносы, приглашения, менеджеры, подписка
# ---------------------------------------------------------------------------


def _get_club_and_check_manage(request: HttpRequest, slug: str):
    """Возвращает (club, None) или (None, redirect_response)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_club(request.user, club):
        messages.error(request, "Нет доступа к управлению клубом.")
        return None, redirect("clubs:club_public_detail", slug=slug)
    if not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return None, redirect("clubs:club_public_detail", slug=slug)
    return club, None


@login_required
@require_GET
def members_list(request: HttpRequest, slug: str) -> HttpResponse:
    """Список участников клуба с фильтрами и поиском."""
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
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
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
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
    for m in members:
        writer.writerow(
            [
                m.user.email,
                m.user.first_name or "",
                m.user.last_name or "",
                m.get_role_display(),
                m.get_status_display(),
                m.joined_at.strftime("%Y-%m-%d %H:%M") if m.joined_at else "",
            ]
        )
    return response


@login_required
@require_POST
def member_remove(request: HttpRequest, slug: str, member_id: int) -> HttpResponse:
    """Исключить участника из клуба (status=REMOVED)."""
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
    member = get_object_or_404(ClubMember, pk=member_id, club=club)
    if member.role == ClubMemberRole.ADMIN:
        admin_count = club.members.filter(
            role=ClubMemberRole.ADMIN, status=ClubMemberStatus.ACTIVE
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


@login_required
@require_GET
def club_tournaments_list(request: HttpRequest, slug: str) -> HttpResponse:
    """Список турниров клуба."""
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
    tournaments = club.tournaments.order_by("-start_date")
    return render(
        request,
        "clubs/club_tournaments_list.html",
        {"club": club, "tournaments": tournaments, "is_club_panel": True},
    )


@login_required
@require_http_methods(["GET", "POST"])
def tournament_plan_access(
    request: HttpRequest, slug: str, tournament_id: int
) -> HttpResponse:
    """Настраивает доступ тарифов игроков к конкретному турниру клуба.

    Args:
        request (HttpRequest): HTTP-запрос пользователя.
        slug (str): Slug клуба.
        tournament_id (int): Идентификатор турнира.

    Returns:
        HttpResponse: Страница матрицы доступов или редирект после сохранения.
    """
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
    tournament = get_object_or_404(Tournament, id=tournament_id, club=club)
    plans = list(
        ClubPlayerPlan.objects.filter(club=club, is_active=True).order_by(
            "sort_order", "name"
        )
    )
    if not plans:
        messages.info(request, "Сначала создайте хотя бы один активный тариф игроков.")
        return redirect("clubs:plans_manage", slug=slug)

    access_map = {
        item.plan_id: item
        for item in ClubPlanTournamentAccess.objects.filter(
            tournament=tournament,
            plan_id__in=[p.id for p in plans],
        )
    }

    if request.method == "POST":
        for plan in plans:
            allow_value = request.POST.get(f"plan_{plan.id}")
            is_allowed = allow_value == "on"
            access_obj = access_map.get(plan.id)
            if access_obj:
                if access_obj.is_allowed != is_allowed:
                    access_obj.is_allowed = is_allowed
                    access_obj.save(update_fields=["is_allowed", "updated_at"])
            else:
                ClubPlanTournamentAccess.objects.create(
                    plan=plan,
                    tournament=tournament,
                    is_allowed=is_allowed,
                )
        messages.success(request, "Доступы тарифов к турниру сохранены.")
        return redirect(
            "clubs:tournament_plan_access", slug=slug, tournament_id=tournament.id
        )

    plan_rows = []
    for plan in plans:
        access_obj = access_map.get(plan.id)
        plan_rows.append(
            {
                "plan": plan,
                "is_allowed": access_obj.is_allowed if access_obj else True,
            }
        )

    return render(
        request,
        "clubs/plan_tournament_access.html",
        {
            "club": club,
            "is_club_panel": True,
            "tournament": tournament,
            "plan_rows": plan_rows,
            "can_edit_settings": user_can_edit_club_settings(request.user, club),
            "can_manage_fees": user_can_manage_fees(request.user, club),
            "can_manage_managers": user_can_manage_managers(request.user, club),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def tournament_create(request: HttpRequest, slug: str) -> HttpResponse:
    """Создание турнира клуба (с привязкой club)."""
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
    can_create, limit_msg = club_can_create_tournament_this_month(club)
    sub = get_club_current_subscription(club)
    platform_plan = get_platform_plan(sub.plan) if sub else None
    is_pro = platform_plan and platform_plan.is_open_interclub
    if request.method == "POST":
        form = ClubTournamentCreateForm(
            request.POST,
            request.FILES,
            club=club,
            is_pro=bool(is_pro),
        )
        if form.is_valid() and can_create:
            tournament = form.save(commit=False)
            tournament.club = club
            tournament.status = TournamentStatus.UPCOMING
            tournament.is_open_interclub = bool(
                form.cleaned_data.get("is_open_interclub")
            ) and bool(is_pro)
            tournament.duration = (
                TournamentDuration.SINGLE_DAY
                if form.cleaned_data.get("is_one_day")
                else TournamentDuration.MULTI_DAY
            )
            if not tournament.registration_deadline and tournament.start_date:
                tournament.registration_deadline = timezone.make_aware(
                    datetime.combine(
                        tournament.start_date,
                        time(23, 59),
                    ),
                    timezone.get_current_timezone(),
                )
            tournament.save()
            tournament.allowed_categories.all().delete()
            for category in form.cleaned_data["allowed_categories"]:
                tournament.allowed_categories.create(category=category)
            for player_plan in ClubPlayerPlan.objects.filter(club=club, is_active=True):
                ClubPlanTournamentAccess.objects.get_or_create(
                    plan=player_plan,
                    tournament=tournament,
                    defaults={"is_allowed": True},
                )
            messages.success(request, f"Турнир «{tournament.name}» создан.")
            return redirect("tournament_manage", slug=tournament.slug)
        elif not can_create:
            messages.error(request, limit_msg)
    else:
        form = ClubTournamentCreateForm(
            club=club,
            is_pro=bool(is_pro),
            initial={
                "city": club.city,
                "format": "weekend_day",
                "allowed_categories": ["amateur"],
            },
        )
    return render(
        request,
        "clubs/tournament_create.html",
        {
            "club": club,
            "is_club_panel": True,
            "form": form,
            "can_create": can_create,
            "is_pro": is_pro,
        },
    )


@login_required
@require_POST
def club_tournament_apply(
    request: HttpRequest, slug: str, tournament_id: int
) -> HttpResponse:
    """Подача заявки клуба на межклубный турнир."""
    club = get_object_or_404(Club, slug=slug)
    if not club.members.filter(
        user=request.user,
        role=ClubMemberRole.ADMIN,
        status=ClubMemberStatus.ACTIVE,
    ).exists():
        messages.error(request, "Только администратор клуба может подавать заявки.")
        return redirect(
            "tournament_detail",
            slug=Tournament.objects.filter(id=tournament_id)
            .values_list("slug", flat=True)
            .first()
            or "",
        )

    tournament = get_object_or_404(Tournament, id=tournament_id, is_open_interclub=True)
    if tournament.club_id == club.id:
        messages.error(request, "Нельзя подать заявку на собственный турнир.")
        return redirect("tournament_detail", slug=tournament.slug)

    if ClubTournamentApplication.objects.filter(
        tournament=tournament, applicant_club=club
    ).exists():
        messages.info(request, "Заявка от вашего клуба уже подана.")
        return redirect("tournament_detail", slug=tournament.slug)

    ClubTournamentApplication.objects.create(
        tournament=tournament,
        applicant_club=club,
        status=ClubApplicationStatus.PENDING,
        message=request.POST.get("message", ""),
    )
    messages.success(
        request, f"Заявка от клуба «{club.name}» подана на турнир «{tournament.name}»."
    )
    return redirect("tournament_detail", slug=tournament.slug)


@login_required
@require_GET
def dashboard_rating(request: HttpRequest, slug: str) -> HttpResponse:
    """Таблица рейтинга клуба (панель админа/менеджера)."""
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
    ratings = list(
        ClubRating.objects.filter(club=club)
        .select_related("member", "member__user")
        .order_by("-points")
    )
    for i, r in enumerate(ratings, 1):
        r.display_rank = i
    return render(
        request,
        "clubs/dashboard_rating.html",
        {"club": club, "ratings": ratings, "is_club_panel": True},
    )


@login_required
@require_GET
def dashboard_rating_export(request: HttpRequest, slug: str) -> HttpResponse:
    """Экспорт рейтинга клуба в CSV."""
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
    ratings = list(
        ClubRating.objects.filter(club=club)
        .select_related("member", "member__user")
        .order_by("-points")
    )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="rating_{club.slug}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["place", "email", "name", "points"])
    for i, r in enumerate(ratings, 1):
        name = (r.member.user.get_full_name() or r.member.user.email or "").strip()
        writer.writerow([i, r.member.user.email, name, r.points])
    return response


@login_required
@require_http_methods(["GET", "POST"])
def fees_settings(request: HttpRequest, slug: str) -> HttpResponse:
    """Настройка взноса клуба (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_fees(request.user, club):
        messages.error(request, "Настраивать взносы может только администратор.")
        return redirect("clubs:dashboard", slug=slug)
    if not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return redirect("clubs:club_public_detail", slug=slug)
    fee = (
        ClubMembershipFee.objects.filter(club=club, is_active=True)
        .order_by("-id")
        .first()
    )
    if request.method == "POST":
        form = ClubMembershipFeeSettingsForm(request.POST, instance=fee)
        if form.is_valid():
            new_secret = (form.cleaned_data.get("new_secret_key") or "").strip()
            if fee:
                fee = form.save()
            else:
                fee = form.save(commit=False)
                fee.club = club
                fee.save()
            if new_secret:
                fee.payment_api_key = encrypt_secret(new_secret)
                fee.save(update_fields=["payment_api_key"])
            messages.success(request, "Настройки взноса сохранены.")
            return redirect("clubs:fees_settings", slug=slug)
    else:
        form = ClubMembershipFeeSettingsForm(instance=fee)
    return render(
        request,
        "clubs/fees_settings.html",
        {"club": club, "form": form, "fee": fee, "is_club_panel": True},
    )


@login_required
@require_GET
def fees_payments(request: HttpRequest, slug: str) -> HttpResponse:
    """Список оплат взносов (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_fees(request.user, club):
        return redirect("clubs:dashboard", slug=slug)
    period_filter = request.GET.get("period", "")
    qs = (
        ClubFeePayment.objects.filter(club=club)
        .select_related("member", "member__user", "fee")
        .order_by("-paid_at")
    )
    if period_filter:
        qs = qs.filter(period_label=period_filter)
    fee = ClubMembershipFee.objects.filter(club=club, is_active=True).first()
    mark_form = None
    if fee:
        mark_form = MarkFeePaidForm(club=club, fee=fee)
    return render(
        request,
        "clubs/fees_payments.html",
        {
            "club": club,
            "payments": qs[:100],
            "fee": fee,
            "mark_form": mark_form,
            "is_club_panel": True,
        },
    )


@login_required
@require_POST
def fees_mark_paid(request: HttpRequest, slug: str) -> HttpResponse:
    """Ручная отметка об оплате взноса (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_fees(request.user, club):
        return redirect("clubs:dashboard", slug=slug)
    fee = ClubMembershipFee.objects.filter(club=club, is_active=True).first()
    if not fee:
        messages.error(request, "Сначала настройте взносы.")
        return redirect("clubs:fees_payments", slug=slug)
    form = MarkFeePaidForm(request.POST, club=club, fee=fee)
    if form.is_valid():
        ClubFeePayment.objects.create(
            club=club,
            member=form.cleaned_data["member"],
            fee=fee,
            amount=form.cleaned_data["amount"],
            period_label=form.cleaned_data["period_label"],
            paid_at=timezone.now(),
            method=FeePaymentMethod.MANUAL,
            marked_by=request.user,
        )
        messages.success(request, "Оплата отмечена.")
    else:
        for err in form.errors.values():
            messages.error(request, "; ".join(err))
    return redirect("clubs:fees_payments", slug=slug)


@login_required
@require_GET
def invites_list(request: HttpRequest, slug: str) -> HttpResponse:
    """Список инвайт-ссылок клуба."""
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
    links = list(
        ClubInviteLink.objects.filter(club=club)
        .select_related("created_by")
        .order_by("-created_at")
    )
    for link in links:
        link.full_join_url = request.build_absolute_uri(
            reverse("clubs:join", kwargs={"slug": club.slug}) + "?token=" + link.token
        )
    return render(
        request,
        "clubs/invites_list.html",
        {"club": club, "links": links, "is_club_panel": True},
    )


@login_required
@require_POST
def invite_deactivate(request: HttpRequest, slug: str, pk: int) -> HttpResponse:
    """Деактивировать инвайт-ссылу."""
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir
    link = get_object_or_404(ClubInviteLink, pk=pk, club=club)
    link.is_active = False
    link.save(update_fields=["is_active"])
    messages.success(request, "Ссылка деактивирована.")
    return redirect("clubs:invites_list", slug=slug)


@login_required
@require_GET
def managers_view(request: HttpRequest, slug: str) -> HttpResponse:
    """Назначение/снятие роли менеджера (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_managers(request.user, club):
        messages.error(request, "Управлять менеджерами может только администратор.")
        return redirect("clubs:dashboard", slug=slug)
    if not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return redirect("clubs:club_public_detail", slug=slug)
    members = (
        club.members.filter(status=ClubMemberStatus.ACTIVE)
        .select_related("user")
        .order_by("user__email")
    )
    return render(
        request,
        "clubs/managers_list.html",
        {"club": club, "members": members, "is_club_panel": True},
    )


@login_required
@require_POST
def manager_set_role(request: HttpRequest, slug: str) -> HttpResponse:
    """POST: назначить или снять роль manager."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_managers(request.user, club):
        return redirect("clubs:dashboard", slug=slug)
    member_id = request.POST.get("member_id")
    action = request.POST.get("action")
    if not member_id or action not in ("set_manager", "remove_manager"):
        messages.error(request, "Неверный запрос.")
        return redirect("clubs:managers_list", slug=slug)
    member = get_object_or_404(ClubMember, pk=member_id, club=club)
    if member.role == ClubMemberRole.ADMIN:
        messages.error(request, "Нельзя изменить роль администратора.")
        return redirect("clubs:managers_list", slug=slug)
    if action == "set_manager":
        member.role = ClubMemberRole.MANAGER
        member.save(update_fields=["role"])
        messages.success(request, f"{member.user.email} назначен менеджером.")
    else:
        member.role = ClubMemberRole.PLAYER
        member.save(update_fields=["role"])
        messages.success(request, "Права менеджера сняты.")
    return redirect("clubs:managers_list", slug=slug)


@login_required
@require_GET
def subscription_view(request: HttpRequest, slug: str) -> HttpResponse:
    """Страница подписки клуба (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_edit_club_settings(request.user, club):
        messages.error(request, "Просмотр подписки доступен только администратору.")
        return redirect("clubs:dashboard", slug=slug)
    subscription = get_club_current_subscription(club)
    history = club.subscriptions.order_by("-ends_at")[:10]
    return render(
        request,
        "clubs/subscription.html",
        {
            "club": club,
            "subscription": subscription,
            "history": history,
            "plan_prices": _get_plan_prices_for_subscription(),
            "is_club_panel": True,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def subscription_pay(request: HttpRequest, slug: str) -> HttpResponse:
    """Выбор тарифа и период оплаты, инициация платежа подписки клуба платформе."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_edit_club_settings(request.user, club):
        messages.error(request, "Оплата подписки доступна только администратору.")
        return redirect("clubs:subscription", slug=slug)

    initial_plan = request.GET.get("plan", "").strip()
    initial_period = request.GET.get("period", "").strip()

    if request.method == "POST":
        plan = request.POST.get("plan", "").strip()
        period = request.POST.get("period", "").strip()
        if plan not in (ClubPlan.START, ClubPlan.BASIC, ClubPlan.PRO):
            messages.error(request, "Выберите корректный тариф.")
            return redirect("clubs:subscription_pay", slug=slug)
        if period not in ("monthly", "yearly"):
            messages.error(request, "Выберите период оплаты (месяц или год).")
            return redirect("clubs:subscription_pay", slug=slug)

        platform_plan = get_platform_plan(plan)
        if not platform_plan:
            messages.error(request, "Тариф не найден. Обратитесь в поддержку.")
            return redirect("clubs:subscription_pay", slug=slug)
        amount = platform_plan.get_price_for_period(period)
        if amount <= 0:
            messages.error(request, "Неверная сумма для выбранного тарифа.")
            return redirect("clubs:subscription_pay", slug=slug)

        period_label = "ежемесячно" if period == "monthly" else "ежегодно"
        plan_name = dict(ClubPlan.choices).get(plan, plan)
        description = f"Подписка клуба {club.name}, тариф {plan_name}, {period_label}"
        return_url = request.build_absolute_uri(
            reverse("clubs:subscription_return", kwargs={"slug": slug})
        )

        try:
            payment_id, confirmation_url = create_payment(
                amount=f"{amount:.2f}",
                return_url=return_url,
                description=description,
                metadata={
                    "subscription_type": "club",
                    "club_id": str(club.id),
                    "plan": plan,
                    "period": period,
                },
                customer_email=club.email,
            )
        except (ValueError, RuntimeError) as e:
            logger.warning("Ошибка создания платежа подписки клуба: %s", e)
            messages.error(
                request, "Не удалось создать платёж. Проверьте настройки ЮKassa."
            )
            return redirect("clubs:subscription_pay", slug=slug)

        ClubSubscriptionPaymentPending.objects.create(
            payment_id=payment_id,
            club=club,
            plan=plan,
            period=period,
            amount=amount,
        )
        return redirect(confirmation_url)

    context = {
        "club": club,
        "plan_prices": _get_plan_prices_for_subscription(),
        "plans": ClubPlan,
    }
    if initial_plan in (ClubPlan.START, ClubPlan.BASIC, ClubPlan.PRO):
        context["initial_plan"] = initial_plan
    if initial_period in ("monthly", "yearly"):
        context["initial_period"] = initial_period

    context["is_club_panel"] = True
    return render(request, "clubs/subscription_pay.html", context)


@login_required
@require_GET
def subscription_return(request: HttpRequest, slug: str) -> HttpResponse:
    """Return URL после оплаты подписки клуба платформе."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_edit_club_settings(request.user, club):
        messages.error(request, "Нет доступа.")
        return redirect("clubs:subscription", slug=slug)

    # ЮKassa может возвращать идентификатор платежа в разных параметрах query
    # или вовсе не добавлять его в URL (редирект без параметров).
    # Сначала пытаемся прочитать его из query-параметров, а если не получилось,
    # берём последний ожидающий платёж для этого клуба.
    payment_id = (
        request.GET.get("payment_id")
        or request.GET.get("orderId")
        or request.GET.get("paymentId")
    )
    if not payment_id:
        pending_fallback = (
            ClubSubscriptionPaymentPending.objects.filter(club=club)
            .order_by("-id")
            .first()
        )
        if not pending_fallback:
            messages.warning(request, "ID платежа не найден в запросе.")
            return redirect("clubs:subscription", slug=slug)
        payment_id = pending_fallback.payment_id

    pending = ClubSubscriptionPaymentPending.objects.filter(
        payment_id=payment_id, club=club
    ).first()
    if not pending:
        messages.warning(request, "Платёж не найден или уже обработан.")
        return redirect("clubs:subscription", slug=slug)

    from apps.payments.yookassa_client import get_payment_status

    status = get_payment_status(payment_id)
    if status != "succeeded":
        messages.error(request, "Оплата не прошла. Попробуйте снова.")
        pending.delete()
        return redirect("clubs:subscription", slug=slug)

    # Создаём новую подписку клуба
    now = timezone.now()
    duration_days = 30 if pending.period == "monthly" else 365
    ends_at = now + timezone.timedelta(days=duration_days)

    # Закрываем предыдущие активные подписки
    ClubSubscription.objects.filter(
        club=club, status=ClubSubscriptionStatus.ACTIVE
    ).update(status=ClubSubscriptionStatus.EXPIRED)

    ClubSubscription.objects.create(
        club=club,
        plan=pending.plan,
        period=(
            ClubSubscriptionPeriod.MONTHLY
            if pending.period == "monthly"
            else ClubSubscriptionPeriod.YEARLY
        ),
        price=pending.amount,
        started_at=now,
        ends_at=ends_at,
        auto_renew=False,
        payment_provider="yookassa",
        payment_ref=payment_id,
        status=ClubSubscriptionStatus.ACTIVE,
    )
    pending.delete()
    messages.success(request, "Подписка успешно оплачена и активирована!")
    return redirect("clubs:subscription", slug=slug)


@login_required
@require_POST
def my_fees_pay(request: HttpRequest) -> HttpResponse:
    """Инициация оплаты членского взноса игроком через ЮKassa (счёт клуба)."""
    member = _get_current_club_member(request)
    if not member:
        messages.error(request, "Клуб не найден.")
        return redirect("clubs:register_choice")

    club = member.club
    fee = (
        ClubMembershipFee.objects.filter(club=club, is_active=True)
        .order_by("-id")
        .first()
    )
    if (
        not fee
        or not fee.payment_provider
        or not fee.payment_shop_id
        or not fee.payment_api_key
    ):
        messages.error(request, "Онлайн-оплата взносов не настроена в этом клубе.")
        return redirect("clubs:my_fees")

    period_label = get_current_period_label(fee)
    if ClubFeePayment.objects.filter(
        member=member, fee=fee, period_label=period_label
    ).exists():
        messages.info(request, "Взнос за этот период уже оплачен.")
        return redirect("clubs:my_fees")

    try:
        secret = decrypt_secret(fee.payment_api_key)
    except Exception as e:
        logger.warning("Не удалось расшифровать секретный ключ клуба: %s", e)
        messages.error(
            request, "Ошибка настройки оплаты. Обратитесь к администратору клуба."
        )
        return redirect("clubs:my_fees")

    amount_str = f"{fee.amount:.2f}"
    description = f"Членский взнос {club.name}, {period_label}"
    return_url = request.build_absolute_uri(reverse("clubs:my_fees_return"))
    metadata = {
        "club_id": str(club.id),
        "fee_id": str(fee.id),
        "member_id": str(member.id),
        "period_label": period_label,
    }

    try:
        payment_id, confirmation_url = create_payment_with_credentials(
            shop_id=fee.payment_shop_id,
            secret_key=secret,
            amount=amount_str,
            return_url=return_url,
            description=description,
            metadata=metadata,
            customer_email=request.user.email,
        )
    except (ValueError, RuntimeError) as e:
        logger.warning("Ошибка создания платежа взноса: %s", e)
        messages.error(request, "Не удалось создать платёж. Проверьте настройки клуба.")
        return redirect("clubs:my_fees")

    ClubFeePaymentPending.objects.create(
        payment_id=payment_id,
        club=club,
        fee=fee,
        member=member,
        amount=fee.amount,
        period_label=period_label,
    )
    return redirect(confirmation_url)


@login_required
@require_GET
def my_fees_return(request: HttpRequest) -> HttpResponse:
    """Return URL после оплаты членского взноса игроком."""
    member = _get_current_club_member(request)
    if not member:
        messages.error(request, "Клуб не найден.")
        return redirect("clubs:register_choice")

    # Аналогично return URL подписки, пытаемся прочитать ID платежа из
    # нескольких возможных имён параметров, которые может вернуть ЮKassa.
    payment_id = (
        request.GET.get("payment_id")
        or request.GET.get("orderId")
        or request.GET.get("paymentId")
    )
    if not payment_id:
        messages.warning(request, "ID платежа не найден.")
        return redirect("clubs:my_fees")

    pending = ClubFeePaymentPending.objects.filter(
        payment_id=payment_id, member=member
    ).first()
    if not pending:
        messages.warning(request, "Платёж не найден или уже обработан.")
        return redirect("clubs:my_fees")

    fee = pending.fee
    try:
        secret = decrypt_secret(fee.payment_api_key)
    except Exception as e:
        logger.warning("Не удалось расшифровать секретный ключ при return: %s", e)
        messages.error(request, "Ошибка проверки платежа.")
        return redirect("clubs:my_fees")

    status = get_payment_status_with_credentials(
        payment_id, fee.payment_shop_id, secret
    )
    if status != "succeeded":
        messages.error(request, "Оплата не прошла. Попробуйте снова.")
        pending.delete()
        return redirect("clubs:my_fees")

    if not ClubFeePayment.objects.filter(payment_ref=payment_id).exists():
        ClubFeePayment.objects.create(
            club=pending.club,
            member=pending.member,
            fee=pending.fee,
            amount=pending.amount,
            period_label=pending.period_label,
            paid_at=timezone.now(),
            method=FeePaymentMethod.ONLINE,
            payment_ref=payment_id,
        )
        try:
            send_fee_paid_notification(
                pending.club, pending.member, pending.amount, pending.period_label
            )
        except Exception:
            logger.exception("Ошибка отправки уведомления об оплате взноса")

    pending.delete()
    messages.success(request, "Оплата взноса прошла успешно!")
    return redirect("clubs:my_fees")


# ---------------------------------------------------------------------------
# Настройки уведомлений (ЛК игрока и панель клуба)
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def my_notification_settings(request: HttpRequest) -> HttpResponse:
    """Настройки уведомлений игрока для текущего клуба."""
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    club = member.club
    obj, _ = ClubNotificationSettings.objects.get_or_create(
        user=request.user, club=club
    )

    if request.method == "POST":
        form = ClubNotificationSettingsForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Настройки уведомлений сохранены.")
            return redirect("clubs:my_notification_settings")
    else:
        form = ClubNotificationSettingsForm(instance=obj)

    return render(
        request,
        "clubs/my_notification_settings.html",
        {"club": club, "form": form, "is_club_panel": True},
    )


@login_required
@require_http_methods(["GET", "POST"])
def club_notification_config(request: HttpRequest, slug: str) -> HttpResponse:
    """Глобальные настройки уведомлений клуба (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_edit_club_settings(request.user, club):
        messages.error(request, "Настройки уведомлений доступны только администратору.")
        return redirect("clubs:dashboard", slug=slug)

    config, _ = ClubNotificationConfig.objects.get_or_create(club=club)

    if request.method == "POST":
        form = ClubNotificationConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "Настройки уведомлений клуба сохранены.")
            return redirect("clubs:club_notification_config", slug=slug)
    else:
        form = ClubNotificationConfigForm(instance=config)

    return render(
        request,
        "clubs/club_notification_config.html",
        {"club": club, "form": form, "is_club_panel": True},
    )


@login_required
@require_GET
def interclub_applications(request: HttpRequest, slug: str) -> HttpResponse:
    """Список межклубных заявок на турниры этого клуба."""
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir

    interclub_tournaments = Tournament.objects.filter(club=club, is_open_interclub=True)
    applications = (
        ClubTournamentApplication.objects.filter(tournament__in=interclub_tournaments)
        .select_related("tournament", "applicant_club", "responded_by")
        .order_by("-created_at")
    )

    status_filter = request.GET.get("status")
    if status_filter in (
        ClubApplicationStatus.PENDING,
        ClubApplicationStatus.APPROVED,
        ClubApplicationStatus.REJECTED,
    ):
        applications = applications.filter(status=status_filter)

    return render(
        request,
        "clubs/interclub_applications.html",
        {
            "club": club,
            "is_club_panel": True,
            "applications": applications,
            "current_status_filter": status_filter or "",
        },
    )


@login_required
@require_POST
def interclub_application_respond(
    request: HttpRequest, slug: str, pk: int
) -> HttpResponse:
    """Одобрить или отклонить заявку клуба на межклубный турнир."""
    club, redir = _get_club_and_check_manage(request, slug)
    if redir:
        return redir

    application = get_object_or_404(
        ClubTournamentApplication.objects.select_related("tournament"),
        pk=pk,
        tournament__club=club,
    )

    action = request.POST.get("action")
    if action == "approve":
        application.status = ClubApplicationStatus.APPROVED
        application.responded_by = request.user
        application.responded_at = timezone.now()
        application.save(update_fields=["status", "responded_by", "responded_at"])
        messages.success(
            request, f"Заявка клуба «{application.applicant_club.name}» одобрена."
        )
    elif action == "reject":
        application.status = ClubApplicationStatus.REJECTED
        application.responded_by = request.user
        application.responded_at = timezone.now()
        application.save(update_fields=["status", "responded_by", "responded_at"])
        messages.success(
            request, f"Заявка клуба «{application.applicant_club.name}» отклонена."
        )
    else:
        messages.error(request, "Неизвестное действие.")

    return redirect("clubs:interclub_applications", slug=slug)


@csrf_exempt
@require_POST
def club_fee_webhook(request: HttpRequest) -> HttpResponse:
    """Webhook от ЮKassa для фиксации оплаты взноса (идемпотентный)."""
    import json

    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception as e:
        logger.warning("Webhook: не удалось распарсить body: %s", e)
        return JsonResponse({"status": "error"}, status=400)

    event_type = body.get("event")
    if event_type != "payment.succeeded":
        return JsonResponse({"status": "ok"}, status=200)

    payment_obj = body.get("object") or {}
    payment_id = payment_obj.get("id")
    status = payment_obj.get("status")
    metadata = payment_obj.get("metadata") or {}

    if not payment_id or status != "succeeded":
        return JsonResponse({"status": "ok"}, status=200)

    club_id = metadata.get("club_id")
    fee_id = metadata.get("fee_id")
    member_id = metadata.get("member_id")
    period_label = metadata.get("period_label")

    if not all([club_id, fee_id, member_id, period_label]):
        logger.warning("Webhook: отсутствуют метаданные для платежа %s", payment_id)
        return JsonResponse({"status": "ok"}, status=200)

    # Идемпотентность: если платёж уже записан — не дублируем
    if ClubFeePayment.objects.filter(payment_ref=payment_id).exists():
        return JsonResponse({"status": "ok"}, status=200)

    try:
        club = Club.objects.get(id=int(str(club_id)))
        fee = ClubMembershipFee.objects.get(id=int(str(fee_id)), club=club)
        member = ClubMember.objects.get(id=int(str(member_id)), club=club)
    except Exception as e:
        logger.warning("Webhook: объекты не найдены для платежа %s: %s", payment_id, e)
        return JsonResponse({"status": "ok"}, status=200)

    amount_obj = payment_obj.get("amount") or {}
    amount_value = amount_obj.get("value", "0")
    try:
        amount = Decimal(str(amount_value))
    except Exception:
        amount = fee.amount

    ClubFeePayment.objects.create(
        club=club,
        member=member,
        fee=fee,
        amount=amount,
        period_label=period_label,
        paid_at=timezone.now(),
        method=FeePaymentMethod.ONLINE,
        payment_ref=payment_id,
    )
    logger.info("Webhook: создан ClubFeePayment для payment_id=%s", payment_id)

    period_label_str = str(period_label)

    try:
        send_fee_paid_notification(club, member, amount, period_label_str)
    except Exception:
        logger.exception(
            "Webhook: ошибка уведомления об оплате взноса payment_id=%s", payment_id
        )

    return JsonResponse({"status": "ok"}, status=200)
