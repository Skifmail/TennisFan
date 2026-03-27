"""
Core views - main pages.
"""

import csv
import json
import logging
import re
import secrets
from calendar import monthrange
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import linebreaks
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_safe

from apps.clubs.models import (
    Club,
    ClubApplicationStatus,
    ClubJoinRequestStatus,
    ClubMemberStatus,
    ClubStatus,
    ClubSubscription,
    ClubSubscriptionStatus,
)
from apps.content.models import News, RulesSection
from apps.courts.models import Court
from apps.payments.models import PaymentRecord
from apps.subscriptions.models import UserSubscription
from apps.tournaments.models import (
    Match,
    SeasonArchive,
    Tournament,
    TournamentDuration,
    TournamentGender,
    TournamentStatus,
)
from apps.training.models import Coach
from apps.users.models import Player, SkillLevel
from apps.users.rating_utils import rating_to_ntrp_level

from . import telegram_support as tg_support
from .forms import FeedbackForm
from .models import City, SupportMessage, SupportMessageAdminDelivery, UserTelegramLink

logger = logging.getLogger(__name__)


def _make_aware_datetime(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> datetime:
    """Создаёт timezone-aware datetime в текущем часовом поясе."""
    naive_value = datetime(year, month, day, hour, minute, second)
    return cast(
        datetime,
        timezone.make_aware(naive_value, timezone.get_current_timezone()),
    )


@login_required
def platform_dashboard(request: HttpRequest) -> HttpResponse:
    """Глобальный дашборд платформы для staff/superuser."""
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(
            request, "Доступ к панели платформы есть только у администратора."
        )
        return redirect("home")

    now = timezone.now()
    today = timezone.localdate()
    current_month_start = today.replace(day=1)
    previous_month_end = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    next_14_days = today + timedelta(days=14)
    next_7_days = now + timedelta(days=7)
    last_30_days = now - timedelta(days=30)
    previous_30_days_start = now - timedelta(days=60)
    month_names_ru = (
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь",
    )

    clubs_total = Club.objects.count()
    operational_clubs_count = Club.objects.filter(
        status__in=[ClubStatus.ACTIVE, ClubStatus.TRIAL]
    ).count()
    suspended_clubs_count = Club.objects.filter(status=ClubStatus.SUSPENDED).count()
    new_clubs_30d = Club.objects.filter(created_at__gte=last_30_days).count()
    previous_new_clubs_30d = Club.objects.filter(
        created_at__gte=previous_30_days_start,
        created_at__lt=last_30_days,
    ).count()

    players_total = Player.objects.filter(is_bye=False).count()
    active_player_ids = set(
        Match.objects.filter(
            match_type=Match.MatchType.TOURNAMENT,
            created_at__gte=last_30_days,
        )
        .filter(Q(player1__is_bye=False) | Q(player2__is_bye=False))
        .values_list("player1_id", "player2_id", "partner1_id", "partner2_id")
    )
    active_player_ids_flat = {
        player_id for ids in active_player_ids for player_id in ids if player_id
    }
    previous_active_matches = Match.objects.filter(
        match_type=Match.MatchType.TOURNAMENT,
        created_at__gte=previous_30_days_start,
        created_at__lt=last_30_days,
    ).values_list("player1_id", "player2_id", "partner1_id", "partner2_id")
    previous_active_player_ids = {
        player_id for ids in previous_active_matches for player_id in ids if player_id
    }
    active_players_count = len(active_player_ids_flat)
    previous_active_players_count = len(previous_active_player_ids)
    activity_rate = (
        round((active_players_count / players_total) * 100) if players_total else 0
    )

    tournaments_this_month = Tournament.objects.filter(
        start_date__year=today.year,
        start_date__month=today.month,
    ).count()
    tournaments_previous_month = Tournament.objects.filter(
        start_date__gte=previous_month_start,
        start_date__lte=previous_month_end,
    ).count()
    upcoming_tournaments_qs = (
        Tournament.objects.filter(start_date__gte=today)
        .select_related("club")
        .order_by("start_date", "registration_deadline")
    )
    nearest_tournaments_count = upcoming_tournaments_qs.filter(
        start_date__lte=next_14_days
    ).count()

    active_user_subscriptions = UserSubscription.objects.filter(
        is_active=True,
        end_date__gt=now,
    ).count()
    expiring_user_subscriptions_count = UserSubscription.objects.filter(
        is_active=True,
        end_date__gt=now,
        end_date__lte=next_7_days,
    ).count()
    expiring_club_subscriptions_count = ClubSubscription.objects.filter(
        status=ClubSubscriptionStatus.ACTIVE,
        ends_at__gt=now,
        ends_at__lte=next_7_days,
    ).count()
    ending_trials_count = Club.objects.filter(
        status=ClubStatus.TRIAL,
        trial_ends_at__gt=now,
        trial_ends_at__lte=next_7_days,
    ).count()

    payments_this_month = PaymentRecord.objects.filter(
        status="succeeded",
        paid_at__gte=current_month_start,
    )
    payments_previous_month = PaymentRecord.objects.filter(
        status="succeeded",
        paid_at__gte=previous_month_start,
        paid_at__lte=previous_month_end,
    )
    subscription_revenue_month = payments_this_month.filter(
        payment_type=PaymentRecord.PaymentType.SUBSCRIPTION
    ).aggregate(total=Coalesce(Sum("amount"), Decimal("0")))["total"] or Decimal("0")
    tournament_revenue_month = payments_this_month.filter(
        payment_type=PaymentRecord.PaymentType.TOURNAMENT
    ).aggregate(total=Coalesce(Sum("amount"), Decimal("0")))["total"] or Decimal("0")
    donation_revenue_month = payments_this_month.filter(
        payment_type=PaymentRecord.PaymentType.DONATION
    ).aggregate(total=Coalesce(Sum("amount"), Decimal("0")))["total"] or Decimal("0")
    club_subscription_revenue_month = ClubSubscription.objects.filter(
        started_at__gte=current_month_start,
        price__gt=0,
    ).aggregate(total=Coalesce(Sum("price"), Decimal("0")))["total"] or Decimal("0")
    total_platform_revenue_month = (
        subscription_revenue_month
        + tournament_revenue_month
        + donation_revenue_month
        + club_subscription_revenue_month
    )
    previous_platform_revenue_month = (
        payments_previous_month.aggregate(total=Coalesce(Sum("amount"), Decimal("0")))[
            "total"
        ]
        or Decimal("0")
    ) + (
        ClubSubscription.objects.filter(
            started_at__gte=previous_month_start,
            started_at__lte=previous_month_end,
            price__gt=0,
        ).aggregate(total=Coalesce(Sum("price"), Decimal("0")))["total"]
        or Decimal("0")
    )
    total_platform_revenue = (
        PaymentRecord.objects.filter(status="succeeded").aggregate(
            total=Coalesce(Sum("amount"), Decimal("0"))
        )["total"]
        or Decimal("0")
    ) + (
        ClubSubscription.objects.filter(price__gt=0).aggregate(
            total=Coalesce(Sum("price"), Decimal("0"))
        )["total"]
        or Decimal("0")
    )

    payment_years = {
        dt.year
        for dt in PaymentRecord.objects.filter(status="succeeded").dates(
            "paid_at", "year"
        )
    }
    club_subscription_years = {
        dt.year
        for dt in ClubSubscription.objects.filter(price__gt=0).dates(
            "started_at", "year"
        )
    }
    available_years = {today.year, *payment_years, *club_subscription_years}
    finance_year_options = sorted(
        {int(year) for year in available_years if year},
        reverse=True,
    )
    selected_year = today.year
    selected_year_raw = request.GET.get("finance_year")
    if selected_year_raw and selected_year_raw.isdigit():
        selected_year_candidate = int(selected_year_raw)
        if selected_year_candidate in finance_year_options:
            selected_year = selected_year_candidate

    selected_month: int | None = None
    selected_month_raw = request.GET.get("finance_month")
    if selected_month_raw and selected_month_raw.isdigit():
        selected_month_candidate = int(selected_month_raw)
        if 1 <= selected_month_candidate <= 12:
            selected_month = selected_month_candidate

    if selected_month:
        finance_period_label = f"{month_names_ru[selected_month - 1]} {selected_year}"
        finance_period_start = _make_aware_datetime(
            selected_year,
            selected_month,
            1,
        )
        finance_period_end = _make_aware_datetime(
            selected_year,
            selected_month,
            monthrange(selected_year, selected_month)[1],
            23,
            59,
            59,
        )
        previous_month = 12 if selected_month == 1 else selected_month - 1
        previous_month_year = (
            selected_year - 1 if selected_month == 1 else selected_year
        )
        previous_period_label = (
            f"{month_names_ru[previous_month - 1]} {previous_month_year}"
        )
        previous_period_start = _make_aware_datetime(
            previous_month_year,
            previous_month,
            1,
        )
        previous_period_end = _make_aware_datetime(
            previous_month_year,
            previous_month,
            monthrange(previous_month_year, previous_month)[1],
            23,
            59,
            59,
        )
    else:
        finance_period_label = f"{selected_year} год"
        finance_period_start = _make_aware_datetime(selected_year, 1, 1)
        finance_period_end = _make_aware_datetime(selected_year, 12, 31, 23, 59, 59)
        previous_period_label = f"{selected_year - 1} год"
        previous_period_start = _make_aware_datetime(selected_year - 1, 1, 1)
        previous_period_end = _make_aware_datetime(
            selected_year - 1, 12, 31, 23, 59, 59
        )

    finance_payments = PaymentRecord.objects.filter(
        status="succeeded",
        paid_at__gte=finance_period_start,
        paid_at__lte=finance_period_end,
    )
    finance_previous_payments = PaymentRecord.objects.filter(
        status="succeeded",
        paid_at__gte=previous_period_start,
        paid_at__lte=previous_period_end,
    )
    finance_club_subscriptions = ClubSubscription.objects.filter(
        started_at__gte=finance_period_start,
        started_at__lte=finance_period_end,
        price__gt=0,
    )
    finance_previous_club_subscriptions = ClubSubscription.objects.filter(
        started_at__gte=previous_period_start,
        started_at__lte=previous_period_end,
        price__gt=0,
    )

    finance_subscription_revenue = finance_payments.filter(
        payment_type=PaymentRecord.PaymentType.SUBSCRIPTION
    ).aggregate(total=Coalesce(Sum("amount"), Decimal("0")))["total"] or Decimal("0")
    finance_tournament_revenue = finance_payments.filter(
        payment_type=PaymentRecord.PaymentType.TOURNAMENT
    ).aggregate(total=Coalesce(Sum("amount"), Decimal("0")))["total"] or Decimal("0")
    finance_donation_revenue = finance_payments.filter(
        payment_type=PaymentRecord.PaymentType.DONATION
    ).aggregate(total=Coalesce(Sum("amount"), Decimal("0")))["total"] or Decimal("0")
    finance_donation_revenue_all_time = PaymentRecord.objects.filter(
        status="succeeded",
        payment_type=PaymentRecord.PaymentType.DONATION,
    ).aggregate(total=Coalesce(Sum("amount"), Decimal("0")))["total"] or Decimal("0")
    finance_club_revenue = finance_club_subscriptions.aggregate(
        total=Coalesce(Sum("price"), Decimal("0"))
    )["total"] or Decimal("0")
    finance_total_revenue = (
        finance_subscription_revenue
        + finance_tournament_revenue
        + finance_donation_revenue
        + finance_club_revenue
    )
    finance_previous_total_revenue = (
        finance_previous_payments.aggregate(
            total=Coalesce(Sum("amount"), Decimal("0"))
        )["total"]
        or Decimal("0")
    ) + (
        finance_previous_club_subscriptions.aggregate(
            total=Coalesce(Sum("price"), Decimal("0"))
        )["total"]
        or Decimal("0")
    )

    pending_join_requests = (
        Club.objects.filter(join_requests__status=ClubJoinRequestStatus.PENDING)
        .distinct()
        .count()
    )
    pending_interclub_applications = (
        Club.objects.filter(
            tournament_applications__status=ClubApplicationStatus.PENDING
        )
        .distinct()
        .count()
    )

    low_fill_tournaments_count = 0
    upcoming_tournaments: list[dict[str, Any]] = []
    for tournament in upcoming_tournaments_qs[:5]:
        participants_count = (
            tournament.full_teams_count()
            if tournament.is_doubles()
            else tournament.participants.count()
        )
        target_participants = (
            tournament.max_teams
            if tournament.is_doubles()
            else tournament.max_participants
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
                "needs_attention": needs_attention,
                "host_label": (
                    tournament.club.name if tournament.club_id else "Платформа"
                ),
            }
        )

    recent_clubs = list(Club.objects.order_by("-created_at")[:4])
    clubs_expiring = list(
        ClubSubscription.objects.filter(
            status=ClubSubscriptionStatus.ACTIVE,
            ends_at__gt=now,
            ends_at__lte=next_7_days,
        )
        .select_related("club")
        .order_by("ends_at")[:4]
    )
    top_clubs = list(
        Club.objects.annotate(
            active_members_count=Count(
                "members",
                filter=Q(members__status=ClubMemberStatus.ACTIVE),
                distinct=True,
            ),
            tournaments_month_count=Count(
                "tournaments",
                filter=Q(
                    tournaments__start_date__year=today.year,
                    tournaments__start_date__month=today.month,
                ),
                distinct=True,
            ),
        ).order_by("-active_members_count", "-tournaments_month_count", "name")[:4]
    )

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
    for offset in range(5, -1, -1):
        month_index = current_month_start.month - offset
        month_anchor_year = current_month_start.year + ((month_index - 1) // 12)
        month_anchor_month = ((month_index - 1) % 12) + 1
        month_anchor = current_month_start.replace(
            year=month_anchor_year,
            month=month_anchor_month,
        )
        month_end = (month_anchor.replace(day=28) + timedelta(days=4)).replace(
            day=1
        ) - timedelta(days=1)
        value = Player.objects.filter(
            is_bye=False,
            created_at__date__gte=month_anchor,
            created_at__date__lte=month_end,
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

    attention_items: list[dict[str, str]] = []
    if suspended_clubs_count:
        attention_items.append(
            {
                "tone": "critical",
                "title": "Есть приостановленные клубы",
                "description": f"{suspended_clubs_count} клубов сейчас в suspended-статусе.",
                "action_label": "Клубы в админке",
                "action_url": reverse("admin:clubs_club_changelist"),
            }
        )
    if expiring_club_subscriptions_count:
        attention_items.append(
            {
                "tone": "warning",
                "title": "Истекают подписки клубов",
                "description": f"{expiring_club_subscriptions_count} клубов требуют продления в ближайшие 7 дней.",
                "action_label": "Подписки клубов",
                "action_url": reverse("admin:clubs_clubsubscription_changelist"),
            }
        )
    if ending_trials_count:
        attention_items.append(
            {
                "tone": "warning",
                "title": "Заканчиваются trial-периоды",
                "description": f"{ending_trials_count} клубов скоро перейдут в зону риска.",
                "action_label": "Клубы",
                "action_url": reverse("admin:clubs_club_changelist"),
            }
        )
    if low_fill_tournaments_count:
        attention_items.append(
            {
                "tone": "warning",
                "title": "Турниры с недобором участников",
                "description": f"{low_fill_tournaments_count} ближайших турниров не набрали минимальный состав.",
                "action_label": "Турниры",
                "action_url": reverse("admin:tournaments_tournament_changelist"),
            }
        )
    if pending_interclub_applications:
        attention_items.append(
            {
                "tone": "info",
                "title": "Есть межклубные заявки",
                "description": f"{pending_interclub_applications} клубов ждут ответа по межклубным заявкам.",
                "action_label": "Клубы",
                "action_url": reverse(
                    "admin:clubs_clubtournamentapplication_changelist"
                ),
            }
        )
    if pending_join_requests:
        attention_items.append(
            {
                "tone": "info",
                "title": "Заявки на вступление в клубы",
                "description": f"{pending_join_requests} заявок ожидают решения.",
                "action_label": "Клубы",
                "action_url": reverse("admin:clubs_club_changelist"),
            }
        )
    if expiring_user_subscriptions_count:
        attention_items.append(
            {
                "tone": "info",
                "title": "Истекают подписки игроков",
                "description": f"{expiring_user_subscriptions_count} пользовательских подписок закончатся в 7 дней.",
                "action_label": "Подписки игроков",
                "action_url": reverse(
                    "admin:subscriptions_usersubscription_changelist"
                ),
            }
        )

    status_summary = (
        attention_items[0]["description"]
        if attention_items
        else f"Платформа работает стабильно: {nearest_tournaments_count} стартов на горизонте 14 дней."
    )

    summary_cards = [
        {
            "label": "Клубы на платформе",
            "value": str(clubs_total),
            "delta": f"{new_clubs_30d - previous_new_clubs_30d:+d} к прошлым 30 дням",
            "meta": f"{operational_clubs_count} работают, {suspended_clubs_count} приостановлены",
            "tone": "default",
        },
        {
            "label": "Игроки платформы",
            "value": str(players_total),
            "delta": f"{active_players_count - previous_active_players_count:+d} активных к прошлым 30 дням",
            "meta": f"{activity_rate}% базы участвовали в матчах",
            "tone": "accent",
        },
        {
            "label": "Турниры в этом месяце",
            "value": str(tournaments_this_month),
            "delta": f"{tournaments_this_month - tournaments_previous_month:+d} к прошлому месяцу",
            "meta": f"{nearest_tournaments_count} стартов в следующие 14 дней",
            "tone": "default",
        },
        {
            "label": "Подписки игроков",
            "value": str(active_user_subscriptions),
            "delta": f"{expiring_user_subscriptions_count} истекают скоро",
            "meta": "Активные пользовательские подписки",
            "tone": "default",
        },
        {
            "label": "Выручка платформы",
            "value": f"{total_platform_revenue_month:.0f} ₽",
            "delta": f"{total_platform_revenue_month - previous_platform_revenue_month:+.0f} ₽ к прошлому месяцу",
            "meta": "Все платежи платформы за текущий месяц",
            "tone": (
                "warning"
                if total_platform_revenue_month < previous_platform_revenue_month
                else "default"
            ),
        },
    ]

    finance_summary = (
        f"За период {finance_period_label} платформа собрала {finance_total_revenue:.0f} ₽. "
        f"Изменение к периоду {previous_period_label}: {finance_total_revenue - finance_previous_total_revenue:+.0f} ₽."
    )
    finance_cards = [
        {
            "label": "Подписки игроков",
            "value": f"{finance_subscription_revenue:.0f} ₽",
            "meta": f"Доход от пользовательских подписок за {finance_period_label.lower()}",
            "tone": "accent",
        },
        {
            "label": "Подписки клубов",
            "value": f"{finance_club_revenue:.0f} ₽",
            "meta": f"Доход от подписок клубов за {finance_period_label.lower()}",
            "tone": "default",
        },
        {
            "label": "Турнирные оплаты",
            "value": f"{finance_tournament_revenue:.0f} ₽",
            "meta": f"Платежи за турниры за {finance_period_label.lower()}",
            "tone": "default",
        },
        {
            "label": "Донаты и всё время",
            "value": f"{finance_donation_revenue_all_time:.0f} ₽",
            "meta": f"Суммарно платформа обработала {total_platform_revenue:.0f} ₽",
            "tone": "default",
        },
    ]

    finance_chart_raw: list[dict[str, Any]] = []
    if selected_month:
        days_in_month = monthrange(selected_year, selected_month)[1]
        for day in range(1, days_in_month + 1):
            point_date = _make_aware_datetime(selected_year, selected_month, day).date()
            payment_total = PaymentRecord.objects.filter(
                status="succeeded",
                paid_at__date=point_date,
            ).aggregate(total=Coalesce(Sum("amount"), Decimal("0")))[
                "total"
            ] or Decimal(
                "0"
            )
            club_total = ClubSubscription.objects.filter(
                started_at__date=point_date,
                price__gt=0,
            ).aggregate(total=Coalesce(Sum("price"), Decimal("0")))["total"] or Decimal(
                "0"
            )
            finance_chart_raw.append(
                {
                    "label": f"{day:02d}",
                    "value": int(payment_total + club_total),
                }
            )
    else:
        for month_number in range(1, 13):
            month_start = _make_aware_datetime(selected_year, month_number, 1)
            month_end = _make_aware_datetime(
                selected_year,
                month_number,
                monthrange(selected_year, month_number)[1],
                23,
                59,
                59,
            )
            payment_total = PaymentRecord.objects.filter(
                status="succeeded",
                paid_at__gte=month_start,
                paid_at__lte=month_end,
            ).aggregate(total=Coalesce(Sum("amount"), Decimal("0")))[
                "total"
            ] or Decimal(
                "0"
            )
            club_total = ClubSubscription.objects.filter(
                started_at__gte=month_start,
                started_at__lte=month_end,
                price__gt=0,
            ).aggregate(total=Coalesce(Sum("price"), Decimal("0")))["total"] or Decimal(
                "0"
            )
            finance_chart_raw.append(
                {
                    "label": month_names_ru[month_number - 1][:3],
                    "value": int(payment_total + club_total),
                }
            )
    finance_chart_max = max([point["value"] for point in finance_chart_raw] + [1])
    finance_chart_points = [
        {**point, "height": max(14, round((point["value"] / finance_chart_max) * 100))}
        for point in finance_chart_raw
    ]

    finance_month_options = [
        {"value": month_number, "label": month_names_ru[month_number - 1]}
        for month_number in range(1, 13)
    ]

    context = {
        "status_summary": status_summary,
        "summary_cards": summary_cards,
        "finance_summary": finance_summary,
        "finance_cards": finance_cards,
        "finance_chart_points": finance_chart_points,
        "finance_year_options": finance_year_options,
        "finance_month_options": finance_month_options,
        "selected_finance_year": selected_year,
        "selected_finance_month": selected_month,
        "finance_period_label": finance_period_label,
        "attention_items": attention_items,
        "upcoming_tournaments": upcoming_tournaments,
        "player_growth_points": player_growth_points,
        "recent_clubs": recent_clubs,
        "clubs_expiring": clubs_expiring,
        "top_clubs": top_clubs,
        "activity_rate": activity_rate,
        "nearest_tournaments_count": nearest_tournaments_count,
        "active_user_subscriptions": active_user_subscriptions,
    }
    return render(request, "core/platform_dashboard.html", context)


@login_required
@require_safe
def platform_players_export(request: HttpRequest) -> HttpResponse:
    """Выгружает список игроков платформы в CSV для staff/superuser."""
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(
            request, "Доступ к панели платформы есть только у администратора."
        )
        return redirect("home")

    players = (
        Player.objects.filter(is_bye=False)
        .select_related("user")
        .order_by("user__email")
    )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="platform_players.csv"'
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow(
        [
            "email",
            "first_name",
            "last_name",
            "phone",
            "city",
            "gender",
            "ntrp_level",
            "skill_level",
            "total_points",
            "matches_played",
            "matches_won",
            "created_at",
        ]
    )
    for player in players:
        writer.writerow(
            [
                player.user.email,
                player.user.first_name or "",
                player.user.last_name or "",
                player.user.phone or "",
                player.city or "",
                player.get_gender_display() if player.gender else "",
                player.ntrp_level,
                player.get_skill_level_display(),
                player.total_points,
                player.matches_played,
                player.matches_won,
                (
                    timezone.localtime(player.created_at).strftime("%Y-%m-%d %H:%M")
                    if player.created_at
                    else ""
                ),
            ]
        )
    return response


@require_safe
def api_cities(request: Any) -> JsonResponse:
    """
    API автодополнения городов: GET /api/cities/?q=<query>.
    Возвращает JSON-список названий городов (макс. 10).
    Для PostgreSQL используется TrigramSimilarity; иначе — поиск по вхождению.
    """
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse([], safe=True)

    from django.db import connection

    if connection.vendor == "postgresql":
        from django.contrib.postgres.search import TrigramSimilarity

        cities = (
            City.objects.annotate(similarity=TrigramSimilarity("name", q))
            .filter(similarity__gt=0.2)
            .order_by("-similarity")[:10]
        )
    else:
        cities = City.objects.filter(name__icontains=q).order_by("name")[:10]

    return JsonResponse([c.name for c in cities], safe=False)


@require_safe
def robots_txt(request: Any) -> HttpResponse:
    """
    Отдача robots.txt для поисковых систем.
    Разрешает индексацию, запрещает админку и платёжные переходы, указывает sitemap.
    """
    base_url = getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", "").strip()
    if not base_url:
        domain = getattr(settings, "SITE_DOMAIN", "tennisfan.ru").strip()
        base_url = f"https://{domain}"
    sitemap_url = f"{base_url.rstrip('/')}{reverse('sitemap')}"
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /payments/process/",
        "Disallow: /payments/return/",
        "Disallow: /payments/success/",
        "Disallow: /telegram/",
        "",
        f"Sitemap: {sitemap_url}",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


def _build_recent_matches(limit: int = 10, days: int = 5):
    """Последние завершённые матчи за N дней для виджета на главной."""
    since = timezone.now() - timedelta(days=days)
    matches = (
        Match.objects.filter(
            status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER],
            completed_datetime__gte=since,
        )
        .select_related(
            "tournament__club",
            "player1__user",
            "player2__user",
            "winner__user",
            "team1__player1__user",
            "team2__player1__user",
        )
        .order_by("-completed_datetime", "-pk")[:limit]
    )
    result = []
    for m in matches:
        p1 = m.get_side1_player()
        p2 = m.get_side2_player()
        if not p1 or not p2:
            continue
        if getattr(p1, "is_bye", False) or getattr(p2, "is_bye", False):
            continue
        delta1 = m.rating_delta_player1 or 0.0
        delta2 = m.rating_delta_player2 or 0.0
        r1_after = float(p1.total_points)
        r2_after = float(p2.total_points)
        r1_before = r1_after - delta1
        r2_before = r2_after - delta2
        n1_after = float(p1.ntrp_level or 0)
        n2_after = float(p2.ntrp_level or 0)
        n1_before = float(rating_to_ntrp_level(r1_before))
        n2_before = float(rating_to_ntrp_level(r2_before))
        n1_delta = round(n1_after - n1_before, 1)
        n2_delta = round(n2_after - n2_before, 1)
        avatar1 = p1.avatar.url if (hasattr(p1, "avatar") and p1.avatar) else None
        avatar2 = p2.avatar.url if (hasattr(p2, "avatar") and p2.avatar) else None
        result.append(
            {
                "id": m.pk,
                "player1": m.get_player1_display(),
                "player2": m.get_player2_display(),
                "p1_avatar": avatar1,
                "p2_avatar": avatar2,
                "score": m.score_display,
                "score_column": (m.score_display or "—").replace(" ", "\n"),
                "p1_ntrp": n1_after,
                "p1_ntrp_delta": n1_delta,
                "p1_rating": r1_after,
                "p1_rating_delta": round(delta1, 1),
                "p2_ntrp": n2_after,
                "p2_ntrp_delta": n2_delta,
                "p2_rating": r2_after,
                "p2_rating_delta": round(delta2, 1),
                "date": (
                    m.completed_datetime.strftime("%d.%m.%Y")
                    if m.completed_datetime
                    else ""
                ),
                "club_name": (
                    m.tournament.club.name
                    if m.tournament_id and m.tournament and m.tournament.club_id
                    else ""
                ),
                "match_url": f"/tournaments/match/{m.pk}/",
            }
        )
    return result


def _build_upcoming_matches(limit: int = 10, days: int = 5):
    """Предстоящие матчи на ближайшие N дней для виджета на главной."""
    now = timezone.now()
    until = now + timedelta(days=days)
    matches = (
        Match.objects.filter(
            status__in=[Match.MatchStatus.SCHEDULED, Match.MatchStatus.IN_PROGRESS],
            scheduled_datetime__gte=now,
            scheduled_datetime__lte=until,
        )
        .select_related(
            "tournament__club",
            "player1__user",
            "player2__user",
            "team1__player1__user",
            "team2__player1__user",
        )
        .order_by("scheduled_datetime", "pk")[:limit]
    )
    result: list[dict[str, Any]] = []
    for m in matches:
        p1 = m.get_side1_player()
        p2 = m.get_side2_player()
        if not p1 or not p2:
            continue
        if getattr(p1, "is_bye", False) or getattr(p2, "is_bye", False):
            continue
        avatar1 = p1.avatar.url if (hasattr(p1, "avatar") and p1.avatar) else None
        avatar2 = p2.avatar.url if (hasattr(p2, "avatar") and p2.avatar) else None
        result.append(
            {
                "id": m.pk,
                "player1": m.get_player1_display(),
                "player2": m.get_player2_display(),
                "p1_avatar": avatar1,
                "p2_avatar": avatar2,
                "score": "—",
                "score_column": "—",
                "p1_ntrp": float(p1.ntrp_level or 0),
                "p1_ntrp_delta": 0.0,
                "p1_rating": float(p1.total_points),
                "p1_rating_delta": 0.0,
                "p2_ntrp": float(p2.ntrp_level or 0),
                "p2_ntrp_delta": 0.0,
                "p2_rating": float(p2.total_points),
                "p2_rating_delta": 0.0,
                "date": (
                    m.scheduled_datetime.strftime("%d.%m.%Y %H:%M")
                    if m.scheduled_datetime
                    else ""
                ),
                "club_name": (
                    m.tournament.club.name
                    if m.tournament_id and m.tournament and m.tournament.club_id
                    else ""
                ),
                "match_url": f"/tournaments/match/{m.pk}/",
            }
        )
    return result


def _build_live_results_fallback_cards() -> list[dict[str, str]]:
    """Формирует информационные карточки для виджета, когда нет сыгранных матчей."""
    cards: list[dict[str, str]] = []

    best_player = (
        Player.objects.filter(is_verified=True, is_bye=False)
        .select_related("user")
        .order_by("-total_points")
        .first()
    )
    if best_player:
        cards.append(
            {
                "title": "Лучший игрок платформы",
                "description": f"{best_player} — {best_player.total_points:.1f} рейтинговых очков.",
                "cta_label": "Открыть рейтинг",
                "cta_url": reverse("rating"),
            }
        )

    best_club_player = (
        Player.objects.filter(
            is_verified=True,
            is_bye=False,
            user__club_memberships__status=ClubMemberStatus.ACTIVE,
        )
        .select_related("user")
        .order_by("-total_points")
        .first()
    )
    if best_club_player:
        cards.append(
            {
                "title": "Лидер среди игроков клубов",
                "description": f"{best_club_player} — {best_club_player.total_points:.1f} очков.",
                "cta_label": "Смотреть профиль",
                "cta_url": reverse("profile", kwargs={"pk": best_club_player.pk}),
            }
        )

    newest_court = Court.objects.filter(is_active=True).order_by("-created_at").first()
    if newest_court:
        cards.append(
            {
                "title": "Новый корт на платформе",
                "description": f"{newest_court.name}, {newest_court.city}.",
                "cta_label": "Все корты",
                "cta_url": reverse("court_list"),
            }
        )

    nearest_tournament = (
        Tournament.objects.filter(
            status=TournamentStatus.UPCOMING,
            start_date__gte=timezone.localdate(),
        )
        .filter(models.Q(club__isnull=True) | models.Q(is_open_interclub=True))
        .order_by("start_date")
        .first()
    )
    if nearest_tournament:
        cards.append(
            {
                "title": "Ближайший турнир",
                "description": (
                    f"{nearest_tournament.name} — старт "
                    f"{nearest_tournament.start_date:%d.%m.%Y}."
                ),
                "cta_label": "Перейти к турниру",
                "cta_url": reverse(
                    "tournament_detail", kwargs={"slug": nearest_tournament.slug}
                ),
            }
        )

    latest_news = News.objects.filter(is_published=True).order_by("-created_at").first()
    if latest_news:
        cards.append(
            {
                "title": "Свежая новость",
                "description": latest_news.title,
                "cta_label": "Читать новость",
                "cta_url": reverse("news_detail", kwargs={"slug": latest_news.slug}),
            }
        )

    if not cards:
        cards.append(
            {
                "title": "Матчи скоро появятся",
                "description": (
                    "Сейчас нет завершённых матчей за последние 5 дней. "
                    "Проверьте турниры и запланируйте игру."
                ),
                "cta_label": "Перейти к турнирам",
                "cta_url": reverse("tournament_list"),
            }
        )

    return cards[:4]


def home(request):
    """Home page view. Формирование сеток по дедлайну выполняется по cron (generate_brackets_past_deadlines)."""
    tournaments = (
        Tournament.objects.filter(
            status__in=[TournamentStatus.UPCOMING, TournamentStatus.ACTIVE],
        )
        .filter(models.Q(club__isnull=True) | models.Q(is_open_interclub=True))
        .select_related("court")
        .prefetch_related("participants__user", "allowed_categories")
    )

    upcoming_tournaments = Tournament.objects.filter(
        status=TournamentStatus.UPCOMING,
    ).filter(models.Q(club__isnull=True) | models.Q(is_open_interclub=True))
    upcoming_tournaments = (
        upcoming_tournaments.select_related("court")
        .prefetch_related("allowed_categories")
        .order_by("start_date")[:6]
    )

    city = request.GET.get("city", "")
    category = request.GET.get("category", "")
    gender = request.GET.get("gender", "")
    duration = request.GET.get("duration", "")

    if city:
        tournaments = tournaments.filter(city__icontains=city)
    if category:
        tournaments = tournaments.filter(
            allowed_categories__category=category
        ).distinct()
    if gender:
        tournaments = tournaments.filter(gender=gender)
    if duration:
        tournaments = tournaments.filter(duration=duration)

    tournaments = tournaments.order_by("start_date")

    paginator = Paginator(tournaments, 7)
    page_number = request.GET.get("page")
    tournaments_page = paginator.get_page(page_number)

    # Получаем топ игроков по сезонным очкам
    from django.db.models import Case, F, IntegerField, Value, When

    from apps.tournaments.season_utils import get_current_season

    current_season = get_current_season()
    top_players = (
        Player.objects.filter(is_verified=True, is_bye=False)
        .select_related(
            "user", "user__subscription", "user__subscription__tier", "season_points"
        )
        .annotate(
            season_pts=Case(
                When(
                    season_points__season_name=current_season.name,
                    season_points__season_year=current_season.year,
                    then=F("season_points__current_season_points"),
                ),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by("-season_pts", "-total_points")[:10]
    )

    recent_matches = _build_recent_matches(limit=10, days=5)
    upcoming_matches = _build_upcoming_matches(limit=10, days=5)
    live_results_fallback_cards = _build_live_results_fallback_cards()

    # Метрики для Hero-блока
    def format_number(num):
        """Форматирует число с пробелами для тысяч."""
        return f"{num:,}".replace(",", " ")

    hero_stats = {
        "players_count": format_number(
            Player.objects.filter(is_bye=False, is_verified=True).count()
        ),
        "tournaments_count": format_number(
            Tournament.objects.filter(
                status__in=[TournamentStatus.UPCOMING, TournamentStatus.ACTIVE],
            )
            .filter(models.Q(club__isnull=True) | models.Q(is_open_interclub=True))
            .count()
        ),
        "matches_count": format_number(
            Match.objects.filter(
                status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]
            ).count()
        ),
        "courts_count": format_number(Court.objects.count()),
        "cities_count": format_number(
            Player.objects.exclude(city="").values("city").distinct().count()
        ),
        "coaches_count": format_number(Coach.objects.count()),
    }

    context = {
        "filtered_tournaments": tournaments_page.object_list,
        "tournaments_page": tournaments_page,
        "upcoming_tournaments": upcoming_tournaments,
        "top_players": top_players,
        "recent_matches": recent_matches,
        "recent_matches_json": json.dumps(recent_matches, default=str),
        "upcoming_matches_json": json.dumps(upcoming_matches, default=str),
        "live_results_fallback_cards_json": json.dumps(
            live_results_fallback_cards, default=str
        ),
        "latest_news": News.objects.filter(is_published=True)[:4],
        "hero_stats": hero_stats,
        "current_filters": {
            "city": city,
            "category": category,
            "gender": gender,
            "duration": duration,
        },
        "category_choices": SkillLevel.choices,
        "gender_choices": TournamentGender.choices,
        "duration_choices": TournamentDuration.choices,
    }

    if (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        and request.GET.get("partial") == "tournaments"
    ):
        return render(request, "core/_home_tournaments.html", context)

    return render(request, "core/home.html", context)


@require_safe
def api_recent_matches(request):
    """API: последние матчи за 5 дней для live-тикера."""
    matches = _build_recent_matches(limit=10, days=5)
    return JsonResponse({"matches": matches})


@require_safe
def api_upcoming_matches(request):
    """API: предстоящие матчи на ближайшие 5 дней для live-тикера."""
    matches = _build_upcoming_matches(limit=10, days=5)
    return JsonResponse({"matches": matches})


def rating(request):
    """Player rating page - сортировка по сезонным очкам."""
    from apps.tournaments.season_utils import get_current_season

    city = request.GET.get("city", "")
    skill_level = request.GET.get("skill_level", "") or request.GET.get("category", "")
    search = request.GET.get("q", "")

    current_season = get_current_season()

    # Получаем игроков с сезонными очками (исключаем служебного игрока "Свободный круг")
    players = (
        Player.objects.filter(is_bye=False)
        .select_related(
            "user", "user__subscription", "user__subscription__tier", "season_points"
        )
        .prefetch_related("season_points")
    )

    if city:
        players = players.filter(city__icontains=city)
    if skill_level:
        players = players.filter(skill_level=skill_level)
    if search:
        players = players.filter(
            Q(user__first_name__icontains=search) | Q(user__last_name__icontains=search)
        )

    # Сортируем по сезонным очкам текущего сезона
    # Используем аннотацию для получения сезонных очков
    from django.db.models import Case, F, IntegerField, Value, When

    players = players.annotate(
        season_pts=Case(
            When(
                season_points__season_name=current_season.name,
                season_points__season_year=current_season.year,
                then=F("season_points__current_season_points"),
            ),
            default=Value(0),
            output_field=IntegerField(),
        )
    ).order_by("-season_pts", "-total_points")

    context = {
        "players": players,
        "current_city": city,
        "current_skill_level": skill_level,
        "search_query": search,
        "skill_level_choices": SkillLevel.choices,
        "current_season_display": f"{current_season.name} {current_season.year}",
    }
    return render(request, "core/rating.html", context)


def hall_of_fame(request):
    """Зал Славы - архив результатов сезонов."""
    season_filter = request.GET.get("season", "")

    # Получаем все уникальные сезоны из архива
    seasons = (
        SeasonArchive.objects.values("season_name", "season_year")
        .distinct()
        .order_by("-season_year", "-season_name")
    )

    # Если выбран конкретный сезон, фильтруем
    if season_filter:
        parts = season_filter.split("_")
        if len(parts) == 2:
            try:
                season_name = "Зима" if parts[0] == "winter" else "Лето"
                season_year = int(parts[1])
                archives = (
                    SeasonArchive.objects.filter(
                        season_name=season_name,
                        season_year=season_year,
                    )
                    .select_related("player", "player__user")
                    .order_by("final_rank", "-final_points")
                )
            except (ValueError, KeyError):
                archives = SeasonArchive.objects.none()
        else:
            archives = SeasonArchive.objects.none()
    else:
        # Показываем последний сезон по умолчанию
        if seasons:
            last_season = seasons[0]
            archives = (
                SeasonArchive.objects.filter(
                    season_name=last_season["season_name"],
                    season_year=last_season["season_year"],
                )
                .select_related("player", "player__user")
                .order_by("final_rank", "-final_points")
            )
        else:
            archives = SeasonArchive.objects.none()

    # Формируем список сезонов для выпадающего списка
    season_list = []
    for s in seasons:
        season_key = (
            f"{'winter' if s['season_name'] == 'Зима' else 'summer'}_{s['season_year']}"
        )
        season_display = f"{s['season_name']} {s['season_year']}"
        season_list.append(
            {
                "key": season_key,
                "display": season_display,
                "name": s["season_name"],
                "year": s["season_year"],
            }
        )

    context = {
        "archives": archives,
        "seasons": season_list,
        "current_season": season_filter,
    }
    return render(request, "core/hall_of_fame.html", context)


def results(request):
    """Match results page."""
    matches = (
        Match.objects.filter(status=Match.MatchStatus.COMPLETED)
        .select_related("player1__user", "player2__user", "winner__user", "tournament")
        .order_by("-completed_datetime")[:50]
    )
    return render(request, "core/results.html", {"matches": matches})


def _is_html_content(text: str) -> bool:
    """Проверяет, похож ли текст на HTML (есть теги), чтобы не применять linebreaks."""
    if not text or "<" not in text:
        return False
    return bool(re.search(r"<\s*[a-zA-Z]", text))


def rules(request):
    """Rules page: tournament formats (одноэтапная сетка, олимпийская, круговой) with detailed descriptions. Content is editable via admin (RulesSection)."""
    rules_content = {}
    for s in RulesSection.objects.all():
        body = s.body or ""
        if body and not _is_html_content(body):
            body = linebreaks(body)
        rules_content[s.slug] = body
    return render(request, "core/rules.html", {"rules_content": rules_content})


# ---------------------------------------------------------------------------
# Обратная связь: новая система через Telegram (SupportMessage + UserTelegramLink)
# ---------------------------------------------------------------------------


def _create_support_message_and_send_to_admin(
    request,
    subject: str,
    message: str,
    guest_name: str | None = None,
    guest_contact: str | None = None,
    guest_telegram_username: str | None = None,
):
    """
    Создать SupportMessage, отправить админу в Telegram, сохранить admin_telegram_message_id.
    Возвращает (support_message, telegram_binding_url или None).
    Поддерживает как зарегистрированных пользователей, так и гостей.
    """
    if request.user.is_authenticated:
        # Уникальный токен для каждого сообщения (поле unique=True); для привязки бота используется UserTelegramLink
        support_msg = SupportMessage.objects.create(
            user=request.user,
            guest_binding_token=secrets.token_urlsafe(32),
            subject=(subject or "")[:200],
            text=message,
            is_from_admin=False,
        )
        user_display = request.user.get_full_name() or request.user.email or "—"
        user_email = request.user.email or ""
        is_guest = False
        guest_contact_val = ""
        guest_telegram_val = ""
    else:
        # Незарегистрированный пользователь (гость)
        guest_tg_username = (guest_telegram_username or "").strip().lstrip("@")
        binding_token = None

        # Если гость указал Telegram username, создаем токен привязки
        if guest_tg_username and tg_support.is_telegram_configured():
            binding_token = secrets.token_urlsafe(32)

        support_msg = SupportMessage.objects.create(
            user=None,
            guest_name=(guest_name or "")[:200].strip(),
            guest_contact=(guest_contact or "")[:200].strip(),
            guest_telegram_username=guest_tg_username,
            guest_binding_token=binding_token or "",
            subject=(subject or "")[:200],
            text=message,
            is_from_admin=False,
        )
        user_display = guest_name or "Гость (незарегистрированный пользователь)"
        user_email = guest_contact or ""
        is_guest = True
        guest_contact_val = guest_contact or ""
        guest_telegram_val = guest_tg_username

    text_for_admin = tg_support.format_support_message_to_admin(
        support_message_id=support_msg.pk,
        user_display=user_display,
        user_email=user_email,
        subject=subject,
        text=message,
        source="сайт",
        is_guest=is_guest,
        guest_contact=guest_contact_val,
        guest_telegram_username=guest_telegram_val,
    )
    deliveries = tg_support.send_to_admin_with_deliveries(text_for_admin)
    if deliveries:
        support_msg.admin_telegram_text = text_for_admin
        support_msg.admin_telegram_message_id = deliveries[0][1]
        support_msg.save(
            update_fields=["admin_telegram_message_id", "admin_telegram_text"]
        )
        for admin_chat_id, admin_msg_id in deliveries:
            SupportMessageAdminDelivery.objects.create(
                support_message=support_msg,
                admin_chat_id=admin_chat_id,
                admin_telegram_message_id=admin_msg_id,
            )

    binding_url = None
    if request.user.is_authenticated and tg_support.is_telegram_configured():
        link, _ = UserTelegramLink.objects.get_or_create(
            user=request.user,
            defaults={
                "telegram_chat_id": None,
                "binding_token": secrets.token_urlsafe(32),
            },
        )
        # Если пользователь уже привязал бота, проверяем гостевые сообщения
        if link.telegram_chat_id:
            link.migrate_guest_messages()
        elif link.telegram_chat_id is None:
            token = link.get_or_create_binding_token()
            bot_username = tg_support.get_bot_username()
            if bot_username:
                binding_url = f"https://t.me/{bot_username}?start={token}"
    elif (
        is_guest
        and support_msg.guest_binding_token
        and tg_support.is_telegram_configured()
    ):
        # Для гостя создаем ссылку привязки, если указан Telegram username
        bot_username = tg_support.get_bot_username()
        if bot_username:
            binding_url = (
                f"https://t.me/{bot_username}?start={support_msg.guest_binding_token}"
            )

    return support_msg, binding_url


@login_required
@require_http_methods(["GET", "POST"])
def support_feedback(request):
    """
    Форма обратной связи. POST: сохранить в БД, отправить админу в Telegram,
    показать «Ваше сообщение принято. Ответ придёт в Telegram» и ссылку на привязку при необходимости.
    """
    if request.method == "GET":
        initial = {}
        subject = (request.GET.get("subject") or "").strip()
        message = (request.GET.get("message") or "").strip()
        if subject:
            initial["subject"] = subject[:200]
        if message:
            initial["message"] = message
        form = FeedbackForm(initial=initial)
        return render(request, "core/support_feedback.html", {"form": form})

    form = FeedbackForm(request.POST)
    if not form.is_valid():
        return render(request, "core/support_feedback.html", {"form": form})

    subject = (form.cleaned_data.get("subject") or "").strip()
    message = (form.cleaned_data.get("message") or "").strip()
    _, binding_url = _create_support_message_and_send_to_admin(
        request, subject, message
    )

    return render(
        request,
        "core/support_feedback_success.html",
        {"telegram_binding_url": binding_url},
    )


@require_http_methods(["POST"])
def support_feedback_submit(request):
    """
    API для виджета (JSON): создать SupportMessage, отправить админу.
    Возвращает success и при необходимости telegram_binding_url.
    Поддерживает как зарегистрированных пользователей, так и гостей.
    """
    try:
        if request.content_type and "application/json" in request.content_type:
            data = json.loads(request.body or "{}")
        else:
            data = request.POST
        message = (data.get("message") or "").strip()
        subject = (data.get("subject") or "").strip()
        guest_name = (data.get("guest_name") or "").strip()
        guest_contact = (data.get("guest_contact") or "").strip()
        guest_telegram_username = (data.get("guest_telegram_username") or "").strip()
    except (json.JSONDecodeError, TypeError):
        return JsonResponse(
            {"success": False, "error": "Неверный формат запроса"}, status=400
        )

    if not message:
        return JsonResponse(
            {"success": False, "error": "Введите сообщение."}, status=400
        )

    # Для незарегистрированных пользователей имя обязательно
    if not request.user.is_authenticated:
        if not guest_name:
            return JsonResponse(
                {"success": False, "error": "Введите ваше имя."}, status=400
            )

    try:
        support_msg, binding_url = _create_support_message_and_send_to_admin(
            request,
            subject,
            message,
            guest_name=guest_name,
            guest_contact=guest_contact,
            guest_telegram_username=guest_telegram_username,
        )
    except Exception as e:
        logger.exception(
            "support_feedback_submit failed for user=%s",
            getattr(request.user, "pk", None),
        )
        return JsonResponse(
            {"success": False, "error": f"Ошибка отправки: {e!s}"},
            status=500,
        )

    # Для гостей сохраняем message_id в session для получения истории
    if not request.user.is_authenticated:
        guest_message_ids = request.session.get("feedback_guest_message_ids", [])
        if support_msg.pk not in guest_message_ids:
            guest_message_ids.append(support_msg.pk)
            request.session["feedback_guest_message_ids"] = guest_message_ids
            request.session.modified = True

    payload = {
        "success": True,
        "message_id": support_msg.pk,
    }
    if binding_url:
        payload["telegram_binding_url"] = binding_url
    return JsonResponse(payload)


# ---------------------------------------------------------------------------
# Telegram Webhook: /start, сообщения пользователя, ответы админа
# ---------------------------------------------------------------------------


def _support_webhook_secret_ok(request) -> bool:
    """Проверка секрета webhook бота поддержки (X-Telegram-Bot-Api-Secret-Token)."""
    secret = getattr(settings, "TELEGRAM_SUPPORT_WEBHOOK_SECRET", None) or ""
    if not secret:
        return True
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    return bool(token == secret)


@csrf_exempt
@require_http_methods(["POST"])
def telegram_support_webhook(request):
    """
    Webhook бота поддержки (TELEGRAM_SUPPORT_BOT_TOKEN).
    - /start с токеном: привязка telegram_chat_id к пользователю.
    - Сообщение от пользователя (личный чат): сохранить, переслать админу.
    - Ответ админа (Reply на сообщение): отправить пользователю, пометить «Ответ отправлен».
    - Сообщение админа без Reply: отправить подсказку «выберите сообщение (Reply)».
    """
    if not _support_webhook_secret_ok(request):
        return JsonResponse({"ok": False}, status=403)

    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"ok": True})

    admin_chat_ids = tg_support.get_admin_chat_ids()
    if not admin_chat_ids:
        return JsonResponse({"ok": True})

    message = data.get("message") or {}
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()
    reply_to = message.get("reply_to_message") or {}

    # ----- Ответ администратора (reply на наше сообщение админу) -----
    if reply_to and chat_id in admin_chat_ids and text:
        original_message_id = reply_to.get("message_id")
        if not original_message_id:
            return JsonResponse({"ok": True})

        delivery = (
            SupportMessageAdminDelivery.objects.filter(
                admin_chat_id=chat_id,
                admin_telegram_message_id=original_message_id,
            )
            .select_related("support_message", "support_message__user")
            .first()
        )
        if delivery:
            support_msg = delivery.support_message
        else:
            support_msg = (
                SupportMessage.objects.filter(
                    admin_telegram_message_id=original_message_id,
                )
                .select_related("user")
                .first()
            )
        if not support_msg:
            logger.debug(
                "Webhook: no SupportMessage for chat_id=%s message_id=%s",
                chat_id,
                original_message_id,
            )
            return JsonResponse({"ok": True})

        user = support_msg.user
        is_guest = user is None
        sent_via_telegram = False

        # Если это зарегистрированный пользователь с привязанным Telegram, отправляем ответ
        if user:
            link = getattr(user, "telegram_link", None)
            if link and link.telegram_chat_id:
                safe_text = (
                    text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                )
                tg_support.send_to_user(
                    link.telegram_chat_id, f"📩 <b>Ответ поддержки:</b>\n\n{safe_text}"
                )
                sent_via_telegram = True
        elif is_guest and support_msg.guest_telegram_chat_id:
            # Если гость привязал Telegram, отправляем ответ через бот
            safe_text = (
                text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            tg_support.send_to_user(
                support_msg.guest_telegram_chat_id,
                f"📩 <b>Ответ поддержки:</b>\n\n{safe_text}",
            )
            sent_via_telegram = True

        # Сохраняем ответ админа в БД (guest_binding_token уникален в модели)
        SupportMessage.objects.create(
            user=user,
            guest_binding_token=secrets.token_urlsafe(32),
            guest_name=support_msg.guest_name if is_guest else "",
            guest_contact=support_msg.guest_contact if is_guest else "",
            guest_telegram_username=(
                support_msg.guest_telegram_username if is_guest else ""
            ),
            guest_telegram_chat_id=(
                support_msg.guest_telegram_chat_id if is_guest else None
            ),
            text=text,
            is_from_admin=True,
        )

        # Обновляем сообщение админу с пометкой об отправке ответа
        if support_msg.admin_telegram_text and support_msg.admin_telegram_message_id:
            if is_guest:
                if sent_via_telegram:
                    new_text = (
                        support_msg.admin_telegram_text
                        + "\n\n✅ Ответ отправлен в Telegram"
                    )
                else:
                    new_text = (
                        support_msg.admin_telegram_text
                        + "\n\n✅ Ответ сохранён. Свяжитесь с пользователем по указанным контактам (Telegram не привязан)."
                    )
            else:
                new_text = support_msg.admin_telegram_text + "\n\n✅ Ответ отправлен"
            tg_support.edit_message(chat_id, original_message_id, new_text)
        return JsonResponse({"ok": True})

    # Админ написал /start — первое приветствие (проверка, что бот работает)
    if chat_id in admin_chat_ids and text and not reply_to:
        if text.strip() == "/start":
            tg_support.send_message(chat_id, tg_support.ADMIN_GREETING_SUPPORT)
            return JsonResponse({"ok": True})
        tg_support.send_message(
            chat_id,
            "⚠️ Чтобы ответить пользователю, выберите его сообщение (Reply) и введите ответ.",
        )
        return JsonResponse({"ok": True})

    # ----- /start: привязка по токену или сообщение «уже привязан» -----
    if text.startswith("/start") and message.get("chat", {}).get("type") == "private":
        try:
            chat_id_int = int(chat_id)
        except (ValueError, TypeError):
            return JsonResponse({"ok": True})

        token = ""
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            token = (parts[1] or "").strip()

        if token:
            # Сначала проверяем токен для зарегистрированных пользователей
            link = UserTelegramLink.objects.filter(binding_token=token).first()
            if link:
                link.telegram_chat_id = chat_id_int
                link.binding_token = None
                link.token_created_at = None
                link.save(
                    update_fields=[
                        "telegram_chat_id",
                        "binding_token",
                        "token_created_at",
                    ]
                )
                # Переносим гостевые сообщения к этому пользователю, если они есть
                migrated_count = link.migrate_guest_messages()
                if migrated_count > 0:
                    tg_support.send_message(
                        chat_id_int,
                        f"✅ Ваш аккаунт успешно привязан.\n"
                        f"📨 Найдено и привязано {migrated_count} обращений, отправленных до регистрации.",
                    )
                else:
                    tg_support.send_message(
                        chat_id_int, "✅ Ваш аккаунт успешно привязан."
                    )
            else:
                # Проверяем токен для гостей
                guest_msg = SupportMessage.objects.filter(
                    guest_binding_token=token, user__isnull=True
                ).first()
                if guest_msg:
                    guest_msg.guest_telegram_chat_id = chat_id_int
                    guest_msg.guest_binding_token = ""
                    guest_msg.save(
                        update_fields=["guest_telegram_chat_id", "guest_binding_token"]
                    )
                    tg_support.send_message(
                        chat_id_int,
                        f"✅ Ваш Telegram успешно привязан для получения ответов на обращение #{guest_msg.pk}.\n"
                        f"Администратор сможет ответить вам здесь в Telegram.",
                    )
                else:
                    tg_support.send_message(
                        chat_id_int,
                        "Токен не найден или устарел. Отправьте форму на сайте заново и перейдите по новой ссылке.",
                    )
        else:
            # /start без токена — проверяем, привязан ли уже этот чат
            existing = UserTelegramLink.objects.filter(
                telegram_chat_id=chat_id_int
            ).first()
            if existing:
                tg_support.send_message(chat_id_int, "✅ Ваш аккаунт уже привязан.")
            else:
                # Проверяем, есть ли гостевые сообщения с таким chat_id
                guest_messages = SupportMessage.objects.filter(
                    user__isnull=True, guest_telegram_chat_id=chat_id_int
                ).first()
                if guest_messages:
                    tg_support.send_message(
                        chat_id_int,
                        "Вы отправили обращение как незарегистрированный пользователь. "
                        "Зарегистрируйтесь на сайте и привяжите аккаунт по ссылке из профиля, "
                        "чтобы ваши обращения были привязаны к вашему аккаунту.",
                    )
                else:
                    tg_support.send_message(
                        chat_id_int,
                        tg_support.USER_GREETING_SUPPORT,
                    )
        return JsonResponse({"ok": True})

    # ----- Обычное сообщение от пользователя (личный чат, уже привязан) -----
    if message.get("chat", {}).get("type") == "private" and text:
        try:
            chat_id_int = int(chat_id)
        except (ValueError, TypeError):
            return JsonResponse({"ok": True})

        link = UserTelegramLink.objects.filter(telegram_chat_id=chat_id_int).first()
        if not link:
            tg_support.send_message(
                chat_id_int,
                tg_support.USER_GREETING_SUPPORT,
            )
            return JsonResponse({"ok": True})

        support_msg = SupportMessage.objects.create(
            user=link.user,
            guest_binding_token=secrets.token_urlsafe(32),
            text=text,
            is_from_admin=False,
        )
        user_display = link.user.get_full_name() or link.user.email or "—"
        user_email = link.user.email or ""
        text_for_admin = tg_support.format_support_message_to_admin(
            support_message_id=support_msg.pk,
            user_display=user_display,
            user_email=user_email,
            subject="",
            text=text,
            source="Telegram",
        )
        deliveries = tg_support.send_to_admin_with_deliveries(text_for_admin)
        if deliveries:
            support_msg.admin_telegram_text = text_for_admin
            support_msg.admin_telegram_message_id = deliveries[0][1]
            support_msg.save(
                update_fields=["admin_telegram_message_id", "admin_telegram_text"]
            )
            for admin_chat_id, admin_msg_id in deliveries:
                SupportMessageAdminDelivery.objects.create(
                    support_message=support_msg,
                    admin_chat_id=admin_chat_id,
                    admin_telegram_message_id=admin_msg_id,
                )

        return JsonResponse({"ok": True})

    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# Старые эндпоинты (виджет на сайте — можно переключить на support_feedback_submit)
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def feedback(request):
    """Редирект на форму обратной связи (новая система)."""
    return redirect("support_feedback")


@require_http_methods(["POST"])
def feedback_submit(request):
    """
    API виджета: использует новую систему SupportMessage и возвращает telegram_binding_url при необходимости.
    Поддерживает как зарегистрированных пользователей, так и гостей.
    """
    return support_feedback_submit(request)


@require_safe
def feedback_threads(request):
    """
    API: список обращений пользователя (SupportMessage) для виджета.
    Поддерживает как авторизованных пользователей, так и гостей (через session).
    Для гостей возвращаются и их сообщения, и ответы админа (по совпадению guest_*).
    """
    threads = []
    messages = []

    if request.user.is_authenticated:
        # Для авторизованных пользователей - получаем все их сообщения и ответы админа
        messages = SupportMessage.objects.filter(user=request.user).order_by(
            "created_at"
        )[:50]
    else:
        # Для гостей - сообщения из session + ответы админа с тем же guest_*
        guest_message_ids = request.session.get("feedback_guest_message_ids", [])
        if guest_message_ids:
            guest_msgs = list(
                SupportMessage.objects.filter(
                    pk__in=guest_message_ids,
                    user__isnull=True,
                ).values_list("guest_name", "guest_contact", "guest_telegram_username")
            )
            triplets = set(guest_msgs)
            q = Q(pk__in=guest_message_ids)
            for gn, gc, gt in triplets:
                q = q | Q(
                    user__isnull=True,
                    guest_name=gn,
                    guest_contact=gc,
                    guest_telegram_username=gt,
                )
            messages = SupportMessage.objects.filter(q).order_by("created_at")[:50]

    current_thread = []
    for m in messages:
        current_thread.append(
            {
                "id": m.pk,
                "text": m.text,
                "is_from_admin": m.is_from_admin,
                "created_at": m.created_at.isoformat(),
            }
        )
    if current_thread:
        threads.append({"messages": current_thread})
    return JsonResponse({"threads": threads})


@require_safe
def private_chat_access(request):
    """
    Обработка ссылки на закрытый канал @TennisFanru.
    Проверяет подписку пользователя и либо создаёт одноразовую ссылку,
    либо показывает сообщение о необходимости приобрести подписку.
    """
    if not request.user.is_authenticated:
        return render(
            request,
            "core/private_chat_access.html",
            {
                "has_access": False,
                "reason": "Необходимо войти в аккаунт.",
                "is_authenticated": False,
            },
            status=403,
        )

    from apps.telegram_bot import services as bot_services
    from apps.telegram_bot.private_chat import get_private_chat_access_status

    has_access, reason = get_private_chat_access_status(request.user)

    if not has_access:
        return render(
            request,
            "core/private_chat_access.html",
            {
                "has_access": False,
                "reason": reason,
                "is_authenticated": True,
            },
            status=403,
        )

    # Создаём одноразовую ссылку на канал
    logger.info(
        "private_chat_access: creating invite link for user_id=%s, username=%s",
        request.user.id,
        request.user.username,
    )
    invite_link = bot_services.create_private_chat_invite_link(
        expire_seconds=1800, member_limit=1
    )

    if not invite_link:
        logger.error(
            "private_chat_access: failed to create invite link for user_id=%s",
            request.user.id,
        )
        return render(
            request,
            "core/private_chat_access.html",
            {
                "has_access": True,
                "error": "Не удалось создать ссылку для входа. Возможно, бот не имеет необходимых прав администратора канала. Обратитесь в поддержку.",
                "is_authenticated": True,
            },
            status=500,
        )

    logger.info(
        "private_chat_access: successfully created invite link for user_id=%s",
        request.user.id,
    )

    # Редиректим на одноразовую ссылку
    return redirect(invite_link)
