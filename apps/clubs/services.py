"""
Сервисы клубного раздела: создание клуба с trial, работа с инвайтами и участниками.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, cast

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

from .models import (
    Club,
    ClubFeePayment,
    ClubJoinRequest,
    ClubJoinRequestStatus,
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
    PlatformPlan,
    PlatformSettings,
)

# Дней до конца периода, при которых показываем «истекает через N дней»
FEE_EXPIRING_DAYS = 7


def get_platform_plan(slug: str) -> PlatformPlan | None:
    """
    Возвращает тариф платформы по slug (start, basic, pro).

    Args:
        slug: Код тарифа, совпадающий с ClubPlan.

    Returns:
        PlatformPlan или None, если не найден.
    """
    return cast(
        PlatformPlan | None,
        PlatformPlan.objects.filter(slug=slug, is_active=True).first(),
    )


def get_platform_plans() -> list[PlatformPlan]:
    """Возвращает список активных тарифов платформы."""
    return list(
        PlatformPlan.objects.filter(is_active=True).order_by("sort_order", "slug")
    )


def club_is_operational(club: Club) -> bool:
    """
    Проверяет, может ли клуб использовать функции платформы (не приостановлен).

    Returns:
        False если клуб suspended или trial истёк; True иначе.
    """
    if club.status == ClubStatus.SUSPENDED:
        return False
    if club.status == ClubStatus.TRIAL and club.trial_ends_at:
        if club.trial_ends_at <= timezone.now():
            return False
    return True


def club_has_public_page_access(club: Club) -> bool:
    """
    Проверяет, доступна ли публичная страница клуба.

    Учитывает только настройки клуба (is_public) и статус/истечение trial.
    Тариф платформы (START/BASIC/PRO) на доступность страницы не влияет:
    публичная страница должна быть доступна на всех тарифах.

    Returns:
        True если страницу можно показывать.
    """
    return bool(club.is_public and club_is_operational(club))


def club_can_add_member(club: Club) -> tuple[bool, str]:
    """
    Проверяет, может ли клуб добавить участника (лимит по тарифу).

    Учитываются участники со статусом ACTIVE и INVITED.

    Returns:
        (True, '') если можно; (False, сообщение) если лимит исчерпан.
    """
    sub = get_club_current_subscription(club)
    plan_slug: str = sub.plan if sub else "start"
    platform_plan = get_platform_plan(plan_slug)
    if not platform_plan or platform_plan.max_members is None:
        return True, ""
    count = club.members.filter(
        status__in=(ClubMemberStatus.ACTIVE, ClubMemberStatus.INVITED)
    ).count()
    if count >= platform_plan.max_members:
        return (
            False,
            f"Лимит участников по тарифу «{platform_plan.name}»: {platform_plan.max_members}. Сейчас: {count}.",
        )
    return True, ""


def get_joinable_club_catalog(
    user: AbstractUser,
    *,
    search: str = "",
    city: str = "",
) -> list[dict[str, Any]]:
    """Возвращает каталог публичных клубов с состоянием CTA для пользователя."""
    clubs_qs = Club.objects.filter(is_public=True).annotate(
        active_members_count=Count(
            "members",
            filter=Q(members__status=ClubMemberStatus.ACTIVE),
            distinct=True,
        )
    )

    if search:
        clubs_qs = clubs_qs.filter(
            Q(name__icontains=search)
            | Q(description__icontains=search)
            | Q(address__icontains=search)
        )
    if city:
        clubs_qs = clubs_qs.filter(city__icontains=city)

    clubs = list(clubs_qs.order_by("name"))
    if not clubs:
        return []

    member_states = {
        item["club_id"]: item["status"]
        for item in ClubMember.objects.filter(user=user).values("club_id", "status")
    }
    pending_request_ids = set(
        ClubJoinRequest.objects.filter(
            user=user,
            status=ClubJoinRequestStatus.PENDING,
        ).values_list("club_id", flat=True)
    )

    items: list[dict[str, Any]] = []
    for club in clubs:
        if not club_has_public_page_access(club):
            continue

        can_add, limit_message = club_can_add_member(club)
        membership_status = member_states.get(club.id)
        item: dict[str, Any] = {
            "club": club,
            "members_count": club.active_members_count,
            "action": "request",
            "action_label": "Подать заявку",
            "action_disabled": False,
            "action_message": "",
        }

        if membership_status == ClubMemberStatus.ACTIVE:
            item["action"] = "member"
            item["action_label"] = "Вы участник"
            item["action_disabled"] = True
        elif membership_status == ClubMemberStatus.INVITED:
            item["action"] = "invite"
            item["action_label"] = "Есть приглашение"
        elif club.id in pending_request_ids:
            item["action"] = "pending"
            item["action_label"] = "Заявка отправлена"
            item["action_disabled"] = True
        elif not can_add:
            item["action"] = "closed"
            item["action_label"] = "Набор закрыт"
            item["action_disabled"] = True
            item["action_message"] = limit_message

        items.append(item)

    return items


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
    platform_plan = get_platform_plan("start")
    trial_days = platform_plan.trial_days if platform_plan else ps.trial_days
    trial_ends = now + timedelta(days=trial_days)

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

    Учитываются только подписки со status=ACTIVE и ends_at > now.

    Returns:
        ClubSubscription с status=active и не истёкшим сроком или None.
    """
    now = timezone.now()
    sub = (
        club.subscriptions.filter(
            status=ClubSubscriptionStatus.ACTIVE,
            ends_at__gt=now,
        )
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
    plan_slug: str = sub.plan if sub else "start"
    platform_plan = get_platform_plan(plan_slug)
    limit = platform_plan.max_tournaments_per_month if platform_plan else None
    if limit is None:
        return True, ""
    today = timezone.now().date()
    count = club.tournaments.filter(
        start_date__year=today.year,
        start_date__month=today.month,
    ).count()
    if count >= limit:
        plan_label = platform_plan.name if platform_plan else str(plan_slug)
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
