"""
Сервисы клубного раздела: создание клуба с trial, работа с инвайтами и участниками.
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast

from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.core.text_search import filter_field_contains_ci

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

from apps.users.models import Notification

from .models import (
    Club,
    ClubFeePayment,
    ClubJoinRequest,
    ClubJoinRequestStatus,
    ClubLegalDocument,
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
from .notifications import send_new_member_notification

# Дней до конца периода, при которых показываем «истекает через N дней»
FEE_EXPIRING_DAYS = 7
logger = logging.getLogger(__name__)


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
        now = timezone.now()
        if club.trial_ends_at <= now:
            has_active_sub = club.subscriptions.filter(
                status=ClubSubscriptionStatus.ACTIVE,
                ends_at__gt=now,
            ).exists()
            return bool(has_active_sub)
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
    city = (city or "").strip()
    if city:
        clubs_qs = filter_field_contains_ci(
            clubs_qs, "city", city, annotation="_join_club_city_l"
        )

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
        raise ValueError("Населённый пункт, адрес и email обязательны")

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

        ClubLegalDocument.objects.get_or_create(
            club=club,
            defaults={
                "title": f"Публичная оферта клуба «{club.name}»",
                "content": "",
                "version": "1.0",
                "is_published": False,
            },
        )

    try:
        from apps.core.telegram_notify import notify_club_registered

        notify_club_registered(club, user)
    except Exception:
        pass

    return club


def assign_club_administrator(
    club: Club,
    user: AbstractUser,
    *,
    reviewed_by: AbstractUser | None = None,
) -> tuple[ClubMember, Literal["created", "promoted", "already_admin"]]:
    """
    Назначает пользователя администратором клуба.

    Если участника ещё нет — создаёт активную запись с ролью ADMIN и рейтинг.
    Если запись есть — повышает роль до ADMIN и активирует участие.
    Открытые заявки на вступление этого пользователя закрываются как одобренные,
    чтобы их последующее одобрение не сбросило роль до игрока.

    Args:
        club: Клуб, которому назначается администратор.
        user: Пользователь платформы.
        reviewed_by: Кто назначил администратора (для закрытия заявок).

    Returns:
        Кортеж ``(участник, вид изменения)``: ``created``, ``promoted``
        или ``already_admin``.
    """
    now = timezone.now()
    with transaction.atomic():
        member, created = ClubMember.objects.select_for_update().get_or_create(
            club=club,
            user=user,
            defaults={
                "role": ClubMemberRole.ADMIN,
                "status": ClubMemberStatus.ACTIVE,
                "joined_at": now,
            },
        )
        kind: Literal["created", "promoted", "already_admin"]
        if created:
            kind = "created"
        else:
            already_admin = (
                member.role == ClubMemberRole.ADMIN
                and member.status == ClubMemberStatus.ACTIVE
            )
            update_fields: list[str] = []
            if member.role != ClubMemberRole.ADMIN:
                member.role = ClubMemberRole.ADMIN
                update_fields.append("role")
            if member.status != ClubMemberStatus.ACTIVE:
                member.status = ClubMemberStatus.ACTIVE
                update_fields.append("status")
            if member.joined_at is None:
                member.joined_at = now
                update_fields.append("joined_at")
            if update_fields:
                member.save(update_fields=update_fields)
            kind = "already_admin" if already_admin else "promoted"

        ClubRating.objects.get_or_create(
            club=club,
            member=member,
            defaults={"points": 0},
        )
        ClubJoinRequest.objects.filter(
            club=club,
            user=user,
            status=ClubJoinRequestStatus.PENDING,
        ).update(
            status=ClubJoinRequestStatus.APPROVED,
            reviewed_by=reviewed_by,
            reviewed_at=now,
            updated_at=now,
        )
        return member, kind


def approve_club_join_request(
    join_request: ClubJoinRequest,
    *,
    reviewed_by: AbstractUser,
    dashboard_url: str = "",
) -> ClubMember:
    """Одобряет заявку: добавляет игрока в клуб и уведомляет его.

    Args:
        join_request: Заявка на вступление.
        reviewed_by: Кто принял решение.
        dashboard_url: Ссылка на панель клуба для письма администраторам.

    Returns:
        ClubMember: Активный участник клуба.

    Raises:
        ValueError: Если заявка уже обработана.
    """
    now = timezone.now()
    with transaction.atomic():
        locked = (
            ClubJoinRequest.objects.select_for_update()
            .select_related("user", "club")
            .get(pk=join_request.pk)
        )
        if locked.status != ClubJoinRequestStatus.PENDING:
            raise ValueError("Заявка уже обработана.")

        club = locked.club
        member, created = ClubMember.objects.get_or_create(
            club=club,
            user=locked.user,
            defaults={
                "role": ClubMemberRole.PLAYER,
                "status": ClubMemberStatus.ACTIVE,
                "invited_by": reviewed_by,
                "joined_at": now,
            },
        )
        already_active = not created and member.status == ClubMemberStatus.ACTIVE
        if not created:
            update_fields: list[str] = []
            if member.status != ClubMemberStatus.ACTIVE:
                member.status = ClubMemberStatus.ACTIVE
                update_fields.append("status")
            if member.joined_at is None:
                member.joined_at = now
                update_fields.append("joined_at")
            if member.invited_by_id is None:
                member.invited_by = reviewed_by
                update_fields.append("invited_by")
            if member.role not in (ClubMemberRole.ADMIN, ClubMemberRole.MANAGER):
                if member.role != ClubMemberRole.PLAYER:
                    member.role = ClubMemberRole.PLAYER
                    update_fields.append("role")
            if update_fields:
                member.save(update_fields=update_fields)

        ClubRating.objects.get_or_create(
            club=club,
            member=member,
            defaults={"points": 0},
        )

        locked.status = ClubJoinRequestStatus.APPROVED
        locked.reviewed_by = reviewed_by
        locked.reviewed_at = now
        locked.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"]
        )

    if not already_active:
        Notification.objects.create(
            user=locked.user,
            message=f"Ваша заявка на вступление в клуб «{club.name}» одобрена.",
            url=reverse("clubs:club_public_detail", kwargs={"slug": club.slug}),
        )
        try:
            send_new_member_notification(club, member, dashboard_url=dashboard_url)
        except Exception:
            logger.exception("Ошибка отправки уведомления о новом участнике")

    join_request.status = locked.status
    join_request.reviewed_by = locked.reviewed_by
    join_request.reviewed_at = locked.reviewed_at
    return cast(ClubMember, member)


def reject_club_join_request(
    join_request: ClubJoinRequest,
    *,
    reviewed_by: AbstractUser,
) -> ClubJoinRequest:
    """Отклоняет заявку и уведомляет игрока.

    Args:
        join_request: Заявка на вступление.
        reviewed_by: Кто принял решение.

    Returns:
        ClubJoinRequest: Обновлённая заявка.

    Raises:
        ValueError: Если заявка уже обработана.
    """
    now = timezone.now()
    with transaction.atomic():
        locked = (
            ClubJoinRequest.objects.select_for_update()
            .select_related("user", "club")
            .get(pk=join_request.pk)
        )
        if locked.status != ClubJoinRequestStatus.PENDING:
            raise ValueError("Заявка уже обработана.")

        locked.status = ClubJoinRequestStatus.REJECTED
        locked.reviewed_by = reviewed_by
        locked.reviewed_at = now
        locked.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"]
        )

    Notification.objects.create(
        user=locked.user,
        message=f"Ваша заявка на вступление в клуб «{locked.club.name}» отклонена.",
        url=reverse("clubs:club_public_detail", kwargs={"slug": locked.club.slug}),
    )
    join_request.status = locked.status
    join_request.reviewed_by = locked.reviewed_by
    join_request.reviewed_at = locked.reviewed_at
    return cast(ClubJoinRequest, locked)


def club_has_published_offer(club: Club) -> bool:
    """Проверяет, опубликована ли оферта клуба (необходимо для приёма платежей на счёт клуба)."""
    doc = ClubLegalDocument.objects.filter(club=club).first()
    return doc is not None and bool(doc.is_published)


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


def get_fee_period_end_date(fee: ClubMembershipFee) -> date:
    """Возвращает дату окончания текущего календарного периода взноса."""
    today = timezone.now().date()
    if fee.period == FeePeriod.MONTHLY:
        _, last_day = monthrange(today.year, today.month)
        return date(today.year, today.month, last_day)
    if fee.period == FeePeriod.QUARTERLY:
        quarter_last_month = ((today.month - 1) // 3 + 1) * 3
        _, last_day = monthrange(today.year, quarter_last_month)
        return date(today.year, quarter_last_month, last_day)
    if fee.period == FeePeriod.YEARLY:
        return date(today.year, 12, 31)
    _, last_day = monthrange(today.year, today.month)
    return date(today.year, today.month, last_day)


def get_fee_expiring_soon_text(fee: ClubMembershipFee) -> str:
    """Возвращает понятную подпись о сроке окончания текущего периода взноса."""
    end_date = get_fee_period_end_date(fee)
    return f"До {end_date.strftime('%d.%m.%Y')}"


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
    end_date = get_fee_period_end_date(fee)
    days_left = (end_date - today).days
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
