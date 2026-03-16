"""
Сервисы клубного раздела: создание клуба с trial, работа с инвайтами и участниками.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, cast

from django.db import transaction
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

from .models import (
    Club,
    ClubFeePayment,
    ClubMember,
    ClubMemberRole,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubPlan,
    ClubRating,
    ClubStatus,
    ClubSubscription,
    ClubSubscriptionPeriod,
    ClubSubscriptionStatus,
    FeePeriod,
    PlatformAuditLog,
    PlatformSettings,
)

# Дней до конца периода, при которых показываем «истекает через N дней»
FEE_EXPIRING_DAYS = 7

# Лимиты турниров в месяц по тарифу клуба (None — без лимита)
CLUB_PLAN_TOURNAMENTS_PER_MONTH = {
    ClubPlan.START: 1,
    ClubPlan.BASIC: 5,
    ClubPlan.PRO: None,
}


def create_club_with_trial(data: dict[str, Any], user: AbstractUser) -> Club:
    """
    Создаёт клуб с trial-подпиской 14 дней и записывает пользователя как админа.

    Args:
        data: словарь с полями клуба (name, slug, city, address, email, phone,
              admin_name, description, logo при наличии) и опционально plan, period.
        user: пользователь, который становится администратором клуба.

    Returns:
        Созданный экземпляр Club.

    Raises:
        ValueError: при невалидных данных или занятом slug.
    """
    name = (data.get("name") or "").strip()
    slug = (data.get("slug") or "").strip().lower()
    if not name or not slug:
        raise ValueError("Название и slug клуба обязательны")
    if Club.objects.filter(slug=slug).exists():
        raise ValueError("Клуб с таким URL-идентификатором уже существует")

    city = (data.get("city") or "").strip()
    address = (data.get("address") or "").strip()
    email = (data.get("email") or "").strip()
    if not city or not address or not email:
        raise ValueError("Город, адрес и email обязательны")

    admin_name = (data.get("admin_name") or "").strip()
    if not admin_name:
        raise ValueError("ФИО ответственного обязательно")

    now = timezone.now()
    ps = get_platform_settings()
    trial_ends = now + timedelta(days=ps.trial_days)

    with transaction.atomic():
        club = cast(
            Club,
            Club.objects.create(
                name=name,
                slug=slug,
                city=city,
                address=data.get("address", ""),
                email=email,
                phone=(data.get("phone") or "")[:50],
                admin_name=admin_name,
                description=(data.get("description") or "")[:1000],
                is_public=True,
                status=ClubStatus.TRIAL,
                trial_ends_at=trial_ends,
            ),
        )
        # Логотип в Phase 2 не передаём из multi-step (файл не храним в сессии); можно добавить в настройках клуба (Phase 4).

        ClubSubscription.objects.create(
            club=club,
            plan=ClubPlan.START,
            period=ClubSubscriptionPeriod.YEARLY,
            price=0,
            started_at=now,
            ends_at=trial_ends,
            status=ClubSubscriptionStatus.ACTIVE,
        )

        member = ClubMember.objects.create(
            club=club,
            user=user,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
            joined_at=now,
        )

        ClubRating.objects.create(club=club, member=member, points=0)

    return club


def get_club_current_subscription(club: Club) -> ClubSubscription | None:
    """
    Возвращает текущую активную подписку клуба (последнюю по ends_at).

    Returns:
        ClubSubscription с status=active или None.
    """
    sub = (
        club.subscriptions.filter(status=ClubSubscriptionStatus.ACTIVE)
        .order_by("-ends_at")
        .first()
    )
    return cast(ClubSubscription | None, sub)


def user_can_manage_club(user, club: Club) -> bool:
    """Проверяет, может ли пользователь управлять клубом (admin или manager)."""
    if not user or not user.is_authenticated:
        return False
    return bool(
        club.members.filter(
            user=user,
            role__in=(ClubMemberRole.ADMIN, ClubMemberRole.MANAGER),
            status=ClubMemberStatus.ACTIVE,
        ).exists()
    )


def _user_is_club_admin(user, club: Club) -> bool:
    """Проверяет, является ли пользователь администратором клуба."""
    if not user or not user.is_authenticated:
        return False
    return bool(
        club.members.filter(
            user=user,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        ).exists()
    )


def user_can_edit_club_settings(user, club: Club) -> bool:
    """Проверяет, может ли пользователь редактировать настройки клуба (только admin)."""
    return user_can_manage_club(user, club) and _user_is_club_admin(user, club)


def user_can_manage_fees(user, club: Club) -> bool:
    """Проверяет, может ли пользователь управлять взносами (только admin)."""
    return user_can_manage_club(user, club) and _user_is_club_admin(user, club)


def user_can_manage_managers(user, club: Club) -> bool:
    """Проверяет, может ли пользователь назначать менеджеров (только admin)."""
    return user_can_manage_club(user, club) and _user_is_club_admin(user, club)


def club_can_create_tournament_this_month(club: Club) -> tuple[bool, str]:
    """
    Проверяет, может ли клуб создать турнир в текущем месяце (лимит по тарифу).

    Returns:
        (True, '') если можно; (False, сообщение) если лимит исчерпан.
    """
    sub = get_club_current_subscription(club)
    plan = sub.plan if sub else ClubPlan.START
    limit = CLUB_PLAN_TOURNAMENTS_PER_MONTH.get(plan)
    if limit is None:
        return True, ""
    today = timezone.now().date()
    count = club.tournaments.filter(
        start_date__year=today.year,
        start_date__month=today.month,
    ).count()
    if count >= limit:
        plan_label = dict(ClubPlan.choices).get(plan, str(plan))
        return (
            False,
            f"Лимит турниров в месяц по тарифу «{plan_label}»: {limit}. Создано: {count}.",
        )
    return True, ""


def get_current_period_label(fee: ClubMembershipFee) -> str:
    """Метка текущего периода для настройки взноса (календарный период)."""
    today = timezone.now().date()
    if fee.period == FeePeriod.MONTHLY:
        label = today.strftime("%Y-%m")
    elif fee.period == FeePeriod.QUARTERLY:
        q = (today.month - 1) // 3 + 1
        label = f"{today.year}-Q{q}"
    elif fee.period == FeePeriod.YEARLY:
        label = str(today.year)
    else:
        label = today.strftime("%Y-%m")
    return cast(str, label)


def get_fee_status_for_member(club: Club, member: ClubMember) -> str | None:
    """
    Статус взноса для участника: 'paid' | 'unpaid' | 'expiring_soon' | None.
    None — взносы не настроены.
    """
    fee = (
        ClubMembershipFee.objects.filter(club=club, is_active=True)
        .order_by("-id")
        .first()
    )
    if not fee:
        return None
    period_label = get_current_period_label(fee)
    if ClubFeePayment.objects.filter(
        member=member, fee=fee, period_label=period_label
    ).exists():
        return "paid"
    today = timezone.now().date()
    if fee.period == FeePeriod.MONTHLY:
        _, last_day = monthrange(today.year, today.month)
        days_left = last_day - today.day
        if 0 <= days_left <= FEE_EXPIRING_DAYS:
            return "expiring_soon"
    if fee.period == FeePeriod.YEARLY:
        ye = date(today.year, 12, 31)
        days_left = (ye - today).days
        if 0 <= days_left <= FEE_EXPIRING_DAYS:
            return "expiring_soon"
    return "unpaid"


def log_platform_action(
    actor: AbstractUser | None,
    action: str,
    club: Club | None = None,
    details: str = "",
) -> PlatformAuditLog:
    """
    Записывает действие platform_admin в аудит-лог.

    Args:
        actor: пользователь, выполнивший действие.
        action: код действия (из PlatformAuditAction).
        club: клуб, к которому относится действие (опционально).
        details: дополнительные детали в свободной форме.

    Returns:
        Созданная запись PlatformAuditLog.
    """
    return cast(
        PlatformAuditLog,
        PlatformAuditLog.objects.create(
            actor=actor,
            action=action,
            club=club,
            details=details,
        ),
    )


def get_platform_settings() -> PlatformSettings:
    """
    Возвращает единственный экземпляр PlatformSettings.

    Returns:
        Экземпляр PlatformSettings (создаётся при первом вызове).
    """
    return PlatformSettings.load()
