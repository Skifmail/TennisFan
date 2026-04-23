from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.tournaments.models import (
    Match,
    MatchResultProposal,
    Tournament,
    TournamentDuration,
    TournamentEntryPayment,
    TournamentGender,
    TournamentPostpaymentInvoice,
    TournamentStatus,
    TournamentTeam,
    TournamentVariant,
)
from apps.users.models import SkillLevel

from ..finance_services import credit_member_balance
from ..forms import (
    ClubMemberPlanAssignForm,
    ClubPlayerPlanForm,
    ClubProfileEditForm,
    ClubTournamentCreateForm,
)
from ..models import (
    Club,
    ClubApplicationStatus,
    ClubFeePayment,
    ClubJoinRequestStatus,
    ClubLegalDocument,
    ClubMember,
    ClubMemberBalanceTransaction,
    ClubMemberRole,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubPlanTournamentAccess,
    ClubPlayerPlan,
    ClubTournamentApplication,
)
from ..plan_services import (
    assign_member_plan,
    get_member_active_plan,
    get_member_plan_limits,
    restore_member_tournament_limit,
)
from ..services import (
    club_can_create_tournament_this_month,
    club_is_operational,
    get_club_current_subscription,
    get_current_period_label,
    get_fee_status_for_member,
    get_platform_plan,
    user_can_edit_club_settings,
    user_can_manage_club,
    user_can_manage_fees,
    user_can_manage_managers,
)
from .helpers import (
    _build_club_profile_context,
    _get_club_and_check_manage,
    _get_current_club_member,
    _remember_current_club,
    _resolve_club_manage,
)


@login_required
@require_GET
def dashboard(request: HttpRequest, slug: str) -> HttpResponse:
    """Дашборд клуба: статистика и навигация по разделам панели."""
    club = get_object_or_404(Club, slug=slug)
    legal_doc = ClubLegalDocument.objects.filter(club=club).first()
    show_club_legal_banner = legal_doc is None or not legal_doc.is_published
    if not user_can_manage_club(request.user, club):
        messages.error(request, "У вас нет доступа к управлению этим клубом.")
        return redirect("clubs:club_public_detail", slug=slug)
    if not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return redirect("clubs:club_public_detail", slug=slug)
    _remember_current_club(request, club)

    now = timezone.now()
    today = timezone.localdate()
    current_month_start = today.replace(day=1)
    previous_month_end = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    next_14_days = today + timedelta(days=14)
    last_7_days = now - timedelta(days=7)
    last_30_days = today - timedelta(days=30)
    previous_30_days_start = today - timedelta(days=60)

    subscription = get_club_current_subscription(club)
    plan_slug: str = subscription.plan if subscription else "start"
    platform_plan = get_platform_plan(plan_slug)
    members_limit = platform_plan.max_members if platform_plan else None
    tournaments_limit = (
        platform_plan.max_tournaments_per_month if platform_plan else None
    )

    active_members_qs = (
        club.members.filter(status=ClubMemberStatus.ACTIVE)
        .select_related("user", "user__player")
        .order_by("user__first_name", "user__last_name", "user__email")
    )
    active_members = list(active_members_qs)
    members_count = len(active_members)
    player_members = [
        member for member in active_members if hasattr(member.user, "player")
    ]
    player_ids = [member.user.player.id for member in player_members]

    tournaments_count = club.tournaments.count()
    tournaments_this_month = club.tournaments.filter(
        start_date__year=today.year,
        start_date__month=today.month,
    ).count()
    tournaments_previous_month = club.tournaments.filter(
        start_date__gte=previous_month_start,
        start_date__lte=previous_month_end,
    ).count()
    upcoming_tournaments_qs = (
        club.tournaments.filter(start_date__gte=today)
        .annotate(
            participants_count=Count("participants", distinct=True),
            full_teams_count_annotated=Count(
                "teams",
                filter=Q(teams__player2__isnull=False),
                distinct=True,
            ),
        )
        .order_by(
            "start_date",
            "registration_deadline",
        )
    )
    nearest_tournaments_count = upcoming_tournaments_qs.filter(
        start_date__lte=next_14_days
    ).count()

    active_player_ids = set(
        club.tournaments.filter(
            start_date__gte=last_30_days,
            start_date__lte=next_14_days,
            participants__in=player_ids,
        )
        .values_list("participants__id", flat=True)
        .distinct()
    )
    previous_active_player_ids = set(
        club.tournaments.filter(
            start_date__gte=previous_30_days_start,
            start_date__lt=last_30_days,
            participants__in=player_ids,
        )
        .values_list("participants__id", flat=True)
        .distinct()
    )
    active_players_count = len(active_player_ids)
    previous_active_players_count = len(previous_active_player_ids)
    activity_rate = (
        round((active_players_count / members_count) * 100) if members_count else 0
    )

    fee = (
        ClubMembershipFee.objects.filter(club=club, is_active=True)
        .order_by("-id")
        .first()
    )
    fee_active = fee is not None
    paid_members_count = 0
    unpaid_members_count = 0
    collected_fees_amount = Decimal("0")
    expected_fees_amount = Decimal("0")
    outstanding_fees_amount = Decimal("0")
    total_fee_revenue = Decimal("0")
    previous_period_collected_amount = Decimal("0")
    fee_collection_rate = 0
    finance_summary = "Взносы не настроены."
    overdue_members: list[ClubMember] = []
    if fee_active and fee:
        if fee.period == "monthly":
            previous_period_date = current_month_start - timedelta(days=1)
            previous_period_label = previous_period_date.strftime("%Y-%m")
        elif fee.period == "quarterly":
            previous_quarter_date = current_month_start - timedelta(days=90)
            previous_period_label = get_current_period_label(
                ClubMembershipFee(
                    period=fee.period,
                    period_start_day=fee.period_start_day,
                )
            )
            quarter = ((previous_quarter_date.month - 1) // 3) + 1
            previous_period_label = f"{previous_quarter_date.year}-Q{quarter}"
        else:
            previous_period_label = str(today.year - 1)

        current_fee_period = get_current_period_label(fee)
        expected_fees_amount = fee.amount * members_count
        paid_member_ids = set(
            ClubFeePayment.objects.filter(
                club=club,
                fee=fee,
                period_label=current_fee_period,
            ).values_list("member_id", flat=True)
        )
        paid_members_count = len(paid_member_ids)
        unpaid_members_count = max(0, members_count - paid_members_count)
        collected_fees_amount = ClubFeePayment.objects.filter(
            club=club,
            fee=fee,
            period_label=current_fee_period,
        ).aggregate(total=Coalesce(Sum("amount"), Decimal("0")))["total"] or Decimal(
            "0"
        )
        previous_period_collected_amount = ClubFeePayment.objects.filter(
            club=club,
            fee=fee,
            period_label=previous_period_label,
        ).aggregate(total=Coalesce(Sum("amount"), Decimal("0")))["total"] or Decimal(
            "0"
        )
        total_fee_revenue = ClubFeePayment.objects.filter(club=club).aggregate(
            total=Coalesce(Sum("amount"), Decimal("0"))
        )["total"] or Decimal("0")
        outstanding_fees_amount = max(
            Decimal("0"),
            expected_fees_amount - collected_fees_amount,
        )
        fee_collection_rate = (
            round((collected_fees_amount / expected_fees_amount) * 100)
            if expected_fees_amount
            else 0
        )
        finance_summary = (
            f"Собрано {collected_fees_amount:.0f} ₽ из {expected_fees_amount:.0f} ₽ "
            f"за текущий период. Покрытие {fee_collection_rate}%."
        )
        overdue_members = [
            member for member in active_members if member.id not in paid_member_ids
        ][:4]

    pending_join_requests = club.join_requests.filter(
        status=ClubJoinRequestStatus.PENDING
    ).count()
    pending_interclub_applications = club.tournament_applications.filter(
        status=ClubApplicationStatus.PENDING
    ).count()
    active_invites_count = (
        club.invite_links.filter(is_active=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .count()
    )

    member_usage_ratio = (members_count / members_limit) if members_limit else 0
    tournament_usage_ratio = (
        tournaments_this_month / tournaments_limit if tournaments_limit else 0
    )

    recent_members = (
        club.members.filter(status=ClubMemberStatus.ACTIVE)
        .select_related("user")
        .order_by("-joined_at", "-created_at")[:4]
    )
    new_members_week = club.members.filter(
        club=club,
        status=ClubMemberStatus.ACTIVE,
        joined_at__gte=last_7_days,
    ).count()

    low_fill_tournaments_count = 0
    upcoming_tournaments: list[dict[str, Any]] = []
    for tournament in upcoming_tournaments_qs[:5]:
        participants_count = (
            int(getattr(tournament, "full_teams_count_annotated", 0))
            if tournament.is_doubles()
            else int(getattr(tournament, "participants_count", 0))
        )
        target_participants = (
            tournament.max_teams
            if tournament.is_doubles()
            else tournament.max_participants
        )
        fill_ratio = (
            participants_count / target_participants if target_participants else 0
        )
        needs_attention = bool(
            tournament.min_participants
            and participants_count < tournament.min_participants
        )
        if needs_attention:
            low_fill_tournaments_count += 1
        upcoming_tournaments.append(
            {
                "object": tournament,
                "participants_count": participants_count,
                "target_participants": target_participants,
                "fill_ratio": round(fill_ratio * 100) if target_participants else None,
                "needs_attention": needs_attention,
            }
        )

    inactive_members = [
        member
        for member in player_members
        if member.user.player.id not in active_player_ids
    ][:4]

    recent_matches = Match.objects.filter(
        tournament__club=club,
        match_type=Match.MatchType.TOURNAMENT,
    ).select_related(
        "tournament",
        "player1__user",
        "player2__user",
        "partner1__user",
        "partner2__user",
        "team1__player1__user",
        "team1__player2__user",
        "team2__player1__user",
        "team2__player2__user",
    )
    player_match_counts: dict[int, int] = defaultdict(int)
    for match in recent_matches.filter(created_at__gte=now - timedelta(days=30)):
        for player_attr in ("player1", "player2", "partner1", "partner2"):
            player = getattr(match, player_attr, None)
            if player and player.id in active_player_ids:
                player_match_counts[player.id] += 1
    top_active_players = sorted(
        [
            {
                "member": member,
                "matches": player_match_counts.get(member.user.player.id, 0),
            }
            for member in player_members
            if member.user.player.id in player_match_counts
        ],
        key=lambda item: (
            -item["matches"],
            item["member"].user.last_name,
            item["member"].user.first_name,
        ),
    )[:4]

    months_ru = (
        "янв",
        "фев",
        "мар",
        "апр",
        "мая",
        "июн",
        "июл",
        "авг",
        "сен",
        "окт",
        "ноя",
        "дек",
    )
    growth_raw: list[dict[str, Any]] = []
    month_cursor_year = current_month_start.year
    month_cursor_month = current_month_start.month
    for offset in range(5, -1, -1):
        month_index = month_cursor_month - offset
        month_anchor_year = month_cursor_year + ((month_index - 1) // 12)
        month_anchor_month = ((month_index - 1) % 12) + 1
        month_anchor = current_month_start.replace(
            year=month_anchor_year,
            month=month_anchor_month,
        )
        month_end = (month_anchor.replace(day=28) + timedelta(days=4)).replace(
            day=1
        ) - timedelta(days=1)
        value = club.members.filter(
            status=ClubMemberStatus.ACTIVE,
            joined_at__date__gte=month_anchor,
            joined_at__date__lte=month_end,
        ).count()
        growth_raw.append(
            {
                "label": f"{months_ru[month_anchor.month - 1]} {str(month_anchor.year)[-2:]}",
                "value": value,
            }
        )
    growth_max = max([point["value"] for point in growth_raw] + [1])
    player_growth_points = [
        {**point, "height": max(14, round((point["value"] / growth_max) * 100))}
        for point in growth_raw
    ]

    weekly_activity_raw: list[dict[str, Any]] = []
    week_start = today - timedelta(days=today.weekday())
    for offset in range(5, -1, -1):
        start = week_start - timedelta(days=offset * 7)
        end = start + timedelta(days=6)
        value = recent_matches.filter(
            created_at__date__gte=start,
            created_at__date__lte=end,
        ).count()
        weekly_activity_raw.append(
            {
                "label": start.strftime("%d.%m"),
                "value": value,
            }
        )
    activity_max = max([point["value"] for point in weekly_activity_raw] + [1])
    activity_points = [
        {**point, "height": max(14, round((point["value"] / activity_max) * 100))}
        for point in weekly_activity_raw
    ]

    attention_items: list[dict[str, str]] = []
    if tournaments_limit and tournaments_this_month >= tournaments_limit:
        attention_items.append(
            {
                "tone": "critical",
                "title": "Лимит турниров на месяц исчерпан",
                "description": (
                    f"Создано {tournaments_this_month} из {tournaments_limit}. "
                    "Новый турнир сейчас не запустить без изменения тарифа."
                ),
                "action_label": "Подписка",
                "action_url": reverse("clubs:subscription", kwargs={"slug": club.slug}),
            }
        )
    elif tournaments_limit and tournament_usage_ratio >= 0.8:
        attention_items.append(
            {
                "tone": "warning",
                "title": "Лимит турниров близко",
                "description": (
                    f"В этом месяце уже {tournaments_this_month} из {tournaments_limit} турниров."
                ),
                "action_label": "Все турниры",
                "action_url": reverse(
                    "clubs:club_tournaments_list",
                    kwargs={"slug": club.slug},
                ),
            }
        )
    if fee_active and unpaid_members_count:
        attention_items.append(
            {
                "tone": "critical" if unpaid_members_count >= 3 else "warning",
                "title": "Есть неоплаченные взносы",
                "description": f"{unpaid_members_count} игроков не оплатили текущий период.",
                "action_label": "Платежи",
                "action_url": reverse(
                    "clubs:fees_payments",
                    kwargs={"slug": club.slug},
                ),
            }
        )
    if pending_join_requests:
        attention_items.append(
            {
                "tone": "warning",
                "title": "Ожидают заявки на вступление",
                "description": f"{pending_join_requests} заявки ждут решения администратора.",
                "action_label": "Приглашения",
                "action_url": reverse("clubs:invites_list", kwargs={"slug": club.slug}),
            }
        )
    if low_fill_tournaments_count:
        attention_items.append(
            {
                "tone": "warning",
                "title": "Турниры с недобором",
                "description": (
                    f"{low_fill_tournaments_count} ближайших турниров пока не набрали минимальный состав."
                ),
                "action_label": "Турниры",
                "action_url": reverse(
                    "clubs:club_tournaments_list",
                    kwargs={"slug": club.slug},
                ),
            }
        )
    active_postpayment_tournaments = (
        Tournament.objects.filter(
            club=club,
            postpayment_window_started_at__isnull=False,
            bracket_generated=False,
        )
        .annotate(
            pending_postpayment=Count(
                "postpayment_invoices",
                filter=Q(
                    postpayment_invoices__status=TournamentPostpaymentInvoice.Status.PENDING
                ),
            )
        )
        .filter(pending_postpayment__gt=0)
    )
    if active_postpayment_tournaments.exists():
        postpayment_summary = active_postpayment_tournaments.aggregate(
            total_pending=Coalesce(Sum("pending_postpayment"), 0),
            total_tournaments=Count("id"),
        )
        total_pending = int(postpayment_summary["total_pending"] or 0)
        total_tournaments = int(postpayment_summary["total_tournaments"] or 0)
        attention_items.append(
            {
                "tone": "critical",
                "title": "Открыта постоплата турниров",
                "description": (
                    f"Ожидается оплата от {total_pending} игроков в {total_tournaments} турнирах."
                ),
                "action_label": "Турниры",
                "action_url": reverse(
                    "clubs:club_tournaments_list",
                    kwargs={"slug": club.slug},
                ),
            }
        )
    if pending_interclub_applications:
        attention_items.append(
            {
                "tone": "info",
                "title": "Есть межклубные заявки",
                "description": f"{pending_interclub_applications} заявок находятся на рассмотрении.",
                "action_label": "Межклубные",
                "action_url": reverse(
                    "clubs:interclub_applications",
                    kwargs={"slug": club.slug},
                ),
            }
        )
    if active_invites_count:
        attention_items.append(
            {
                "tone": "info",
                "title": "Активные приглашения в работе",
                "description": (
                    f"Сейчас активно {active_invites_count} приглашений или ссылок для вступления."
                ),
                "action_label": "Инвайты",
                "action_url": reverse("clubs:invites_list", kwargs={"slug": club.slug}),
            }
        )
    if members_limit and member_usage_ratio >= 0.85:
        attention_items.append(
            {
                "tone": "warning",
                "title": "Лимит игроков почти заполнен",
                "description": f"Активно {members_count} из {members_limit} игроков по текущему тарифу.",
                "action_label": "Участники",
                "action_url": reverse("clubs:members_list", kwargs={"slug": club.slug}),
            }
        )

    if attention_items:
        status_summary = attention_items[0]["description"]
    elif nearest_tournaments_count:
        status_summary = (
            f"В ближайшие 14 дней запланировано {nearest_tournaments_count} турнира(ов), "
            "клуб работает без критических сигналов."
        )
    else:
        status_summary = "Критических сигналов нет. Можно сфокусироваться на росте игроков и новых запусках."

    summary_cards = [
        {
            "label": "Игроки в клубе",
            "value": str(members_count),
            "delta": (
                f"+{new_members_week} за 7 дней"
                if new_members_week
                else "Без новых вступлений за 7 дней"
            ),
            "meta": (
                f"{members_limit - members_count} мест осталось"
                if members_limit and members_limit >= members_count
                else ("Без лимита по тарифу" if not members_limit else "Лимит заполнен")
            ),
            "tone": "default",
        },
        {
            "label": "Активные игроки",
            "value": f"{active_players_count}",
            "delta": (
                f"{active_players_count - previous_active_players_count:+d} к прошлым 30 дням"
                if previous_active_players_count or active_players_count
                else "Пока нет активности"
            ),
            "meta": f"{activity_rate}% состава участвовали в турнирах",
            "tone": "accent",
        },
        {
            "label": "Турниры в этом месяце",
            "value": str(tournaments_this_month),
            "delta": (
                f"{tournaments_this_month - tournaments_previous_month:+d} "
                "к прошлому месяцу"
            ),
            "meta": (
                f"{tournaments_limit - tournaments_this_month} слотов осталось"
                if tournaments_limit and tournaments_limit >= tournaments_this_month
                else (
                    "Без лимита по тарифу"
                    if not tournaments_limit
                    else "Лимит месяца исчерпан"
                )
            ),
            "tone": "default",
        },
        {
            "label": "Ближайшие турниры",
            "value": str(nearest_tournaments_count),
            "delta": "Следующие 14 дней",
            "meta": (
                f"Следующий старт {upcoming_tournaments[0]['object'].start_date.strftime('%d.%m.%Y')}"
                if upcoming_tournaments
                else "Нет стартов в расписании"
            ),
            "tone": "default",
        },
        {
            "label": "Взносы и оплаты" if fee_active else "Заявки и приглашения",
            "value": (
                f"{paid_members_count}/{members_count}"
                if fee_active
                else str(pending_join_requests + active_invites_count)
            ),
            "delta": (
                f"{collected_fees_amount:.0f} ₽ собрано"
                if fee_active
                else f"{pending_join_requests} заявок, {active_invites_count} инвайтов"
            ),
            "meta": (
                f"{unpaid_members_count} ждут оплату"
                if fee_active
                else "Поток вступлений под контролем"
            ),
            "tone": (
                "warning"
                if (fee_active and unpaid_members_count) or pending_join_requests
                else "default"
            ),
        },
    ]
    finance_cards = [
        {
            "label": "Собрано за период",
            "value": f"{collected_fees_amount:.0f} ₽" if fee_active else "0 ₽",
            "meta": (
                f"{collected_fees_amount - previous_period_collected_amount:+.0f} ₽ к прошлому периоду"
                if fee_active
                else "Подключите клубные взносы"
            ),
            "tone": "accent",
        },
        {
            "label": "Ожидаемый доход",
            "value": f"{expected_fees_amount:.0f} ₽" if fee_active else "0 ₽",
            "meta": (
                f"{paid_members_count} из {members_count} игроков оплатили"
                if fee_active
                else "Нет активного тарифа взносов"
            ),
            "tone": "default",
        },
        {
            "label": "Задолженность",
            "value": f"{outstanding_fees_amount:.0f} ₽" if fee_active else "0 ₽",
            "meta": (
                f"{unpaid_members_count} игроков в долге"
                if fee_active
                else "Долги появятся после настройки взносов"
            ),
            "tone": "warning" if outstanding_fees_amount else "default",
        },
        {
            "label": "Оплачено всего",
            "value": f"{total_fee_revenue:.0f} ₽" if fee_active else "0 ₽",
            "meta": (
                f"Покрытие текущего периода {fee_collection_rate}%"
                if fee_active
                else "История оплат ещё не формируется"
            ),
            "tone": "default",
        },
    ]

    return render(
        request,
        "clubs/dashboard.html",
        {
            "is_club_panel": True,
            "club": club,
            "status_summary": status_summary,
            "members_count": members_count,
            "tournaments_count": tournaments_count,
            "members_limit": members_limit,
            "tournaments_limit": tournaments_limit,
            "subscription": subscription,
            "fee_active": fee_active,
            "summary_cards": summary_cards,
            "finance_cards": finance_cards,
            "finance_summary": finance_summary,
            "attention_items": attention_items,
            "upcoming_tournaments": upcoming_tournaments,
            "recent_members": recent_members,
            "inactive_members": inactive_members,
            "overdue_members": overdue_members,
            "top_active_players": top_active_players,
            "player_growth_points": player_growth_points,
            "activity_points": activity_points,
            "pending_join_requests": pending_join_requests,
            "active_invites_count": active_invites_count,
            "pending_interclub_applications": pending_interclub_applications,
            "activity_rate": activity_rate,
            "unpaid_members_count": unpaid_members_count,
            "paid_members_count": paid_members_count,
            "nearest_tournaments_count": nearest_tournaments_count,
            "can_edit_settings": user_can_edit_club_settings(request.user, club),
            "can_manage_fees": user_can_manage_fees(request.user, club),
            "can_manage_managers": user_can_manage_managers(request.user, club),
            "show_club_legal_banner": show_club_legal_banner,
        },
    )


@login_required
@require_GET
def plans_manage(request: HttpRequest, slug: str) -> HttpResponse:
    """Показывает список клубных тарифов и форму назначения участнику."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    plans = ClubPlayerPlan.objects.filter(club=club).order_by("sort_order", "name")
    assign_form = ClubMemberPlanAssignForm(club=club)
    return render(
        request,
        "clubs/plans_manage.html",
        {
            "is_club_panel": True,
            "club": club,
            "plans": plans,
            "active_plans_count": plans.filter(is_active=True).count(),
            "assign_form": assign_form,
            "can_edit_settings": user_can_edit_club_settings(request.user, club),
            "can_manage_fees": user_can_manage_fees(request.user, club),
            "can_manage_managers": user_can_manage_managers(request.user, club),
        },
    )


@login_required
@require_POST
def plan_toggle_usage(request: HttpRequest, slug: str) -> HttpResponse:
    """Включает или выключает использование клубных тарифов."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

    target_state = str(request.POST.get("enabled") or "").strip().lower()
    club.use_player_plans = target_state in {"1", "true", "on", "yes"}
    club.save(update_fields=["use_player_plans"])

    if club.use_player_plans:
        messages.success(
            request,
            "Система тарифов включена. Ограничения и условия тарифов снова применяются.",
        )
    else:
        messages.success(
            request,
            "Система тарифов выключена. Тарифные ограничения больше не влияют на регистрацию игроков в турниры клуба.",
        )
    return redirect("clubs:plans_manage", slug=slug)


@login_required
@require_http_methods(["GET", "POST"])
def plan_create(request: HttpRequest, slug: str) -> HttpResponse:
    """Создаёт новый тариф игроков для клуба."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

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
    """Редактирует существующий тариф клуба."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
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
    """Назначает тариф выбранному участнику клуба."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

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


@login_required
@require_http_methods(["GET", "POST"])
def club_edit(request: HttpRequest, slug: str) -> HttpResponse:
    """Редактирование профиля клуба (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_edit_club_settings(request.user, club):
        messages.error(
            request,
            "Редактировать настройки клуба может только администратор.",
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


@login_required
@require_GET
def my_dashboard(request: HttpRequest) -> HttpResponse:
    """Отображает личный кабинет игрока внутри текущего клуба."""
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
    context["club_profile_url"] = reverse(
        "clubs:player_profile",
        kwargs={"slug": club.slug, "player_id": player.pk},
    )
    return render(request, "users/profile.html", context)


@login_required
@require_GET
def my_tournaments(request: HttpRequest) -> HttpResponse:
    """Раздел «Мои турниры» для текущего клуба."""
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

    qs = Tournament.objects.none()
    if player is not None:
        qs = (
            Tournament.objects.filter(club=club)
            .filter(
                Q(participants=player)
                | Q(teams__player1=player)
                | Q(teams__player2=player)
            )
            .order_by("start_date")
            .distinct()
        )
    if status_filter == "upcoming":
        qs = qs.filter(status="upcoming")
    elif status_filter == "active":
        qs = qs.filter(status__in=["active", "group_stage", "playoffs"])
    elif status_filter == "completed":
        qs = qs.filter(status="completed")

    fee = ClubMembershipFee.objects.filter(club=club, is_active=True).first()
    fee_status = get_fee_status_for_member(club, member) if fee else None
    fee_restrict = fee and fee.restrict_tournament_access and fee_status != "paid"
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
def my_matches(request: HttpRequest) -> HttpResponse:
    """Раздел «Мои матчи» для текущего клуба."""
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

    status_filter = request.GET.get("status", "upcoming")
    base_q = (
        Q(player1=player)
        | Q(player2=player)
        | Q(team1__player1=player)
        | Q(team1__player2=player)
        | Q(team2__player1=player)
        | Q(team2__player2=player)
    )
    all_matches = list(
        Match.objects.filter(
            base_q,
            tournament__club=club,
            match_type=Match.MatchType.TOURNAMENT,
        )
        .select_related(
            "tournament",
            "tournament__club",
            "player1__user",
            "player2__user",
            "winner__user",
            "team1__player1__user",
            "team1__player2__user",
            "team2__player1__user",
            "team2__player2__user",
            "winner_team__player1__user",
            "winner_team__player2__user",
        )
        .order_by("deadline", "scheduled_datetime", "pk")
        .distinct()
    )
    pending_proposals = MatchResultProposal.objects.filter(
        match__in=all_matches,
        status=Match.ProposalStatus.PENDING,
    ).select_related("proposer", "proposer__user", "match")
    pending_by_match: dict[int, list[MatchResultProposal]] = {}
    for proposal in pending_proposals:
        pending_by_match.setdefault(proposal.match_id, []).append(proposal)

    enriched_matches: list[Match] = []
    for match in all_matches:
        proposals = pending_by_match.get(match.pk, [])
        match.pending_proposals = proposals
        match.has_pending = bool(proposals)
        match.requires_response = any(
            proposal.proposer_id != player.pk for proposal in proposals
        )
        match.awaiting_confirmation = any(
            proposal.proposer_id == player.pk for proposal in proposals
        )
        match.can_submit_result = (
            match.status
            not in (
                Match.MatchStatus.COMPLETED,
                Match.MatchStatus.WALKOVER,
                Match.MatchStatus.CANCELLED,
            )
            and not match.has_pending
        )
        match.next_url = (
            f"{reverse('match_detail', kwargs={'pk': match.pk})}"
            f"?next={reverse('clubs:my_matches')}?status={status_filter}"
        )
        if match.deadline:
            match.display_date = match.deadline
            match.display_date_label = "Дедлайн"
        elif match.scheduled_datetime:
            match.display_date = match.scheduled_datetime
            match.display_date_label = "Матч"
        else:
            match.display_date = None
            match.display_date_label = "Дата"
        if match.requires_response:
            match.club_action_label = "Подтвердить результат"
            match.club_action_hint = "Соперник ждёт вашего ответа"
        elif match.awaiting_confirmation:
            match.club_action_label = "Результат отправлен"
            match.club_action_hint = "Ожидание подтверждения соперником"
        elif match.can_submit_result:
            match.club_action_label = "Внести результат"
            match.club_action_hint = "Матч сыгран, результат ещё не внесён"
        elif match.status in (
            Match.MatchStatus.COMPLETED,
            Match.MatchStatus.WALKOVER,
        ):
            match.club_action_label = "Матч завершён"
            match.club_action_hint = (
                f"Счёт: {match.score_display}"
                if match.score_display != "—"
                else "Результат зафиксирован"
            )
        else:
            match.club_action_label = "Ожидает матча"
            match.club_action_hint = "Следите за дедлайном и временем игры"
        enriched_matches.append(match)

    def _sort_key_upcoming(item: Match) -> tuple[datetime, int]:
        return (
            item.display_date or timezone.now(),
            item.pk,
        )

    def _sort_key_completed(item: Match) -> tuple[datetime, int]:
        return (
            item.display_date or timezone.now(),
            item.pk,
        )

    upcoming_matches = sorted(
        [
            item
            for item in enriched_matches
            if item.status
            not in (
                Match.MatchStatus.COMPLETED,
                Match.MatchStatus.WALKOVER,
                Match.MatchStatus.CANCELLED,
            )
        ],
        key=_sort_key_upcoming,
    )
    actionable_matches = sorted(
        [
            item
            for item in enriched_matches
            if item.can_submit_result
            or item.requires_response
            or item.awaiting_confirmation
        ],
        key=_sort_key_upcoming,
    )
    completed_matches = sorted(
        [
            item
            for item in enriched_matches
            if item.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER)
        ],
        key=_sort_key_completed,
        reverse=True,
    )

    if status_filter == "action":
        matches = actionable_matches
    elif status_filter == "completed":
        matches = completed_matches
    else:
        matches = upcoming_matches

    return render(
        request,
        "clubs/my_matches.html",
        {
            "club": club,
            "is_club_panel": True,
            "matches": matches[:50],
            "status_filter": status_filter,
            "upcoming_count": len(upcoming_matches),
            "action_count": len(actionable_matches),
            "completed_count": len(completed_matches),
        },
    )


@login_required
@require_POST
def my_tournament_cancel(request: HttpRequest, tournament_slug: str) -> HttpResponse:
    """Отменяет запись текущего участника клуба на турнир до старта или формирования сетки."""
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    player = getattr(request.user, "player", None)
    if player is None:
        messages.error(request, "Профиль игрока не найден.")
        return redirect("clubs:my_tournaments")

    tournament = get_object_or_404(
        Tournament,
        slug=tournament_slug,
        club=member.club,
    )
    if tournament.status != TournamentStatus.UPCOMING:
        messages.error(request, "Отменить запись можно только до начала турнира.")
        return redirect("clubs:my_tournaments")
    if getattr(tournament, "bracket_generated", False):
        messages.error(
            request,
            "После формирования групп или сетки отмена записи недоступна.",
        )
        return redirect("clubs:my_tournaments")

    registration_removed = False
    if tournament.is_doubles():
        team = (
            TournamentTeam.objects.filter(tournament=tournament)
            .filter(Q(player1=player) | Q(player2=player))
            .select_related("player1__user", "player2__user")
            .first()
        )
        if not team:
            messages.error(request, "Запись на турнир не найдена.")
            return redirect("clubs:my_tournaments")

        if team.player1_id == player.id and team.player2_id:
            team.player1 = team.player2
            team.player2 = None
            team.save(update_fields=["player1", "player2"])
        elif team.player2_id == player.id:
            team.player2 = None
            team.save(update_fields=["player2"])
        else:
            team.delete()
        registration_removed = True
    else:
        if not tournament.participants.filter(pk=player.pk).exists():
            messages.error(request, "Запись на турнир не найдена.")
            return redirect("clubs:my_tournaments")
        tournament.participants.remove(player)
        registration_removed = True

    restored_limit = False
    paid_entry_qs = TournamentEntryPayment.objects.filter(
        tournament=tournament,
        user=request.user,
    )
    had_paid_entry = paid_entry_qs.exists()
    tournament_has_entry_fee = bool(
        getattr(tournament, "entry_fee", None)
        and float(getattr(tournament, "entry_fee", 0)) > 0
    )
    if (
        registration_removed
        and not tournament.is_one_day
        and (not tournament_has_entry_fee or not had_paid_entry)
    ):
        restored_limit = restore_member_tournament_limit(member)

    refunded_to_balance = False
    if registration_removed and tournament_has_entry_fee and had_paid_entry:
        credit_member_balance(
            member,
            tournament.entry_fee or Decimal("0"),
            source=ClubMemberBalanceTransaction.Source.TOURNAMENT_REFUND,
            description=f"Возврат за отмену турнира «{tournament.name}»",
            reference=f"tournament:{tournament.id}",
            metadata={"tournament_id": tournament.id},
        )
        paid_entry_qs.delete()
        paid_ids = request.session.get("tournament_entry_paid") or []
        if tournament.id in paid_ids:
            request.session["tournament_entry_paid"] = [
                item for item in paid_ids if item != tournament.id
            ]
            request.session.modified = True
        refunded_to_balance = True

    if refunded_to_balance and restored_limit:
        messages.success(
            request,
            "Запись на турнир отменена. Оплаченная сумма возвращена на баланс, лимит регистраций восстановлен.",
        )
    elif refunded_to_balance:
        messages.success(
            request,
            "Запись на турнир отменена. Оплаченная сумма возвращена на баланс клуба.",
        )
    elif restored_limit:
        messages.success(
            request,
            "Запись на турнир отменена. Лимит регистраций по вашему тарифу восстановлен.",
        )
    else:
        messages.success(request, "Запись на турнир отменена.")
    return redirect("clubs:my_tournaments")


@login_required
@require_GET
def club_tournaments_list(request: HttpRequest, slug: str) -> HttpResponse:
    """Список турниров клуба."""
    club = get_object_or_404(Club, slug=slug)
    member = (
        ClubMember.objects.filter(
            club=club,
            user=request.user,
            status=ClubMemberStatus.ACTIVE,
        )
        .select_related("club")
        .first()
    )
    if not member:
        messages.error(request, "Этот раздел доступен только участникам клуба.")
        return redirect("clubs:club_public_detail", slug=slug)

    can_manage = user_can_manage_club(request.user, club)
    if can_manage and not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return redirect("clubs:club_public_detail", slug=slug)

    search = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    category_filter = request.GET.get("category", "").strip()
    gender_filter = request.GET.get("gender", "").strip()
    variant_filter = request.GET.get("variant", "").strip()

    tournaments = (
        club.tournaments.all()
        .annotate(participants_count=Count("participants", distinct=True))
        .prefetch_related(
            "allowed_categories",
            "participants__user",
        )
    )
    tournaments_total = tournaments.count()

    if search:
        tournaments = tournaments.filter(name__icontains=search)

    valid_statuses = {choice[0] for choice in TournamentStatus.choices}
    if status_filter in valid_statuses:
        tournaments = tournaments.filter(status=status_filter)
    else:
        status_filter = ""

    valid_categories = {choice[0] for choice in SkillLevel.choices}
    if category_filter in valid_categories:
        tournaments = tournaments.filter(allowed_categories__category=category_filter)
    else:
        category_filter = ""

    valid_genders = {choice[0] for choice in TournamentGender.choices}
    if gender_filter in valid_genders:
        tournaments = tournaments.filter(gender=gender_filter)
    else:
        gender_filter = ""

    valid_variants = {choice[0] for choice in TournamentVariant.choices}
    if variant_filter in valid_variants:
        tournaments = tournaments.filter(variant=variant_filter)
    else:
        variant_filter = ""

    tournaments = tournaments.distinct().order_by("-start_date")
    paginator = Paginator(tournaments, 20)
    page_number = request.GET.get("page")
    tournaments_page = paginator.get_page(page_number)
    return render(
        request,
        "clubs/club_tournaments_list.html",
        {
            "club": club,
            "tournaments": tournaments_page.object_list,
            "tournaments_page": tournaments_page,
            "tournaments_total": tournaments_total,
            "search": search,
            "status_filter": status_filter,
            "category_filter": category_filter,
            "gender_filter": gender_filter,
            "variant_filter": variant_filter,
            "category_choices": SkillLevel.choices,
            "gender_choices": TournamentGender.choices,
            "variant_choices": TournamentVariant.choices,
            "is_club_panel": True,
            "can_manage_club": can_manage,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def tournament_plan_access(
    request: HttpRequest,
    slug: str,
    tournament_id: int,
) -> HttpResponse:
    """Настраивает доступ тарифов игроков к конкретному турниру клуба."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
    tournament = get_object_or_404(Tournament, id=tournament_id, club=club)
    plans = list(
        ClubPlayerPlan.objects.filter(club=club, is_active=True).order_by(
            "sort_order",
            "name",
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
            "clubs:tournament_plan_access",
            slug=slug,
            tournament_id=tournament.id,
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
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res
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
                    datetime.combine(tournament.start_date, time(23, 59)),
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
        if not can_create:
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
            "page_mode": "create",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def tournament_edit(
    request: HttpRequest,
    slug: str,
    tournament_id: int,
) -> HttpResponse:
    """Редактирование клубного турнира."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

    tournament = get_object_or_404(Tournament, pk=tournament_id, club=club)
    sub = get_club_current_subscription(club)
    platform_plan = get_platform_plan(sub.plan) if sub else None
    is_pro = platform_plan and platform_plan.is_open_interclub
    structure_locked = bool(tournament.bracket_generated)

    if request.method == "POST":
        form = ClubTournamentCreateForm(
            request.POST,
            request.FILES,
            instance=tournament,
            club=club,
            is_pro=bool(is_pro),
        )
        if structure_locked:
            form.lock_structure_fields()
        if form.is_valid():
            tournament = form.save(commit=False)
            tournament.club = club
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
                    datetime.combine(tournament.start_date, time(23, 59)),
                    timezone.get_current_timezone(),
                )
            tournament.save()
            if not structure_locked:
                tournament.allowed_categories.all().delete()
                for category in form.cleaned_data["allowed_categories"]:
                    tournament.allowed_categories.create(category=category)
            messages.success(request, f"Турнир «{tournament.name}» обновлён.")
            return redirect("tournament_manage", slug=tournament.slug)
    else:
        form = ClubTournamentCreateForm(
            instance=tournament,
            club=club,
            is_pro=bool(is_pro),
        )
        if structure_locked:
            form.lock_structure_fields()

    return render(
        request,
        "clubs/tournament_create.html",
        {
            "club": club,
            "is_club_panel": True,
            "form": form,
            "can_create": True,
            "is_pro": is_pro,
            "page_mode": "edit",
            "tournament": tournament,
            "structure_locked": structure_locked,
        },
    )


@login_required
@require_POST
def club_tournament_apply(
    request: HttpRequest,
    slug: str,
    tournament_id: int,
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
        tournament=tournament,
        applicant_club=club,
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
        request,
        f"Заявка от клуба «{club.name}» подана на турнир «{tournament.name}».",
    )
    return redirect("tournament_detail", slug=tournament.slug)


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
    search = (request.GET.get("q") or "").strip()
    active_members = (
        club.members.filter(status=ClubMemberStatus.ACTIVE)
        .select_related("user", "user__player")
        .order_by("user__email")
    )
    managers = active_members.filter(
        role__in=(ClubMemberRole.ADMIN, ClubMemberRole.MANAGER)
    ).order_by("role", "user__first_name", "user__last_name", "user__email")
    candidate_members_qs = active_members.filter(role=ClubMemberRole.PLAYER).order_by(
        "user__first_name", "user__last_name", "user__email"
    )
    candidate_members: list[ClubMember] = []
    candidate_count = 0
    if search:
        search_normalized = search.casefold()
        for member in candidate_members_qs:
            first_name = (member.user.first_name or "").casefold()
            last_name = (member.user.last_name or "").casefold()
            full_name = (
                (f"{member.user.first_name or ''} {member.user.last_name or ''}")
                .strip()
                .casefold()
            )
            email = (member.user.email or "").casefold()
            if (
                search_normalized in first_name
                or search_normalized in last_name
                or search_normalized in full_name
                or search_normalized in email
            ):
                candidate_members.append(member)
    managers_count = managers.filter(role=ClubMemberRole.MANAGER).count()
    admins_count = managers.filter(role=ClubMemberRole.ADMIN).count()
    if search:
        candidate_count = len(candidate_members)
    return render(
        request,
        "clubs/managers_list.html",
        {
            "club": club,
            "managers": managers,
            "candidate_members": candidate_members[:12] if search else [],
            "is_club_panel": True,
            "search": search,
            "managers_total": managers.count(),
            "managers_count": managers_count,
            "admins_count": admins_count,
            "candidate_count": candidate_count,
        },
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
def interclub_applications(request: HttpRequest, slug: str) -> HttpResponse:
    """Список межклубных заявок на турниры этого клуба."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

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
    request: HttpRequest,
    slug: str,
    pk: int,
) -> HttpResponse:
    """Одобрить или отклонить заявку клуба на межклубный турнир."""
    club_or_res = _resolve_club_manage(_get_club_and_check_manage(request, slug))
    if isinstance(club_or_res, HttpResponse):
        return club_or_res
    club = club_or_res

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
            request,
            f"Заявка клуба «{application.applicant_club.name}» одобрена.",
        )
    elif action == "reject":
        application.status = ClubApplicationStatus.REJECTED
        application.responded_by = request.user
        application.responded_at = timezone.now()
        application.save(update_fields=["status", "responded_by", "responded_at"])
        messages.success(
            request,
            f"Заявка клуба «{application.applicant_club.name}» отклонена.",
        )
    else:
        messages.error(request, "Неизвестное действие.")

    return redirect("clubs:interclub_applications", slug=slug)
