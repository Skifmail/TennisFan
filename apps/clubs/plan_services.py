"""Сервисная логика клубных тарифов игроков (Phase 8).

Логика лимитов (аналогично глобальной платформе):

**Однодневные турниры:**
- Тариф не обязателен для регистрации
- Если есть взнос → игрок платит взнос
- Лимит НЕ расходуется

**Многодневные турниры с взносом:**
- Если есть тариф и лимит → регистрация бесплатно, лимит расходуется
- Если нет тарифа или лимит исчерпан → игрок платит взнос

**Многодневные турниры без взноса:**
- Обязателен тариф с доступным лимитом
- Лимит расходуется
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, cast

from django.db import models, transaction
from django.utils import timezone

from .models import (
    ClubMember,
    ClubMemberPlan,
    ClubMemberPlanStatus,
    ClubPlanSlotUsage,
    ClubPlanTournamentAccess,
    ClubPlayerPlan,
    ClubRegistrationLimitPeriod,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

    from apps.tournaments.models import Tournament


class RegistrationMode(Enum):
    """Режим регистрации на клубный турнир."""

    FREE = "free"  # Бесплатно по тарифу (лимит расходуется)
    PAID = "paid"  # Оплата вступительного взноса (лимит НЕ расходуется)
    BLOCKED = "blocked"  # Регистрация невозможна


@dataclass(slots=True, frozen=True)
class RegistrationEligibility:
    """Результат проверки возможности регистрации на турнир.

    Attributes:
        mode: Режим регистрации (FREE/PAID/BLOCKED).
        message: Сообщение для пользователя (ошибка или информация).
        consumes_limit: Будет ли расходоваться лимит при регистрации.
    """

    mode: RegistrationMode
    message: str
    consumes_limit: bool


@dataclass(slots=True, frozen=True)
class MemberPlanLimits:
    """Снимок лимитов участника по клубному тарифу.

    Attributes:
        monthly_tournaments_limit: Лимит турниров на месяц (None — без лимита).
        tournaments_used: Уже использовано турниров в текущем месяце.
        tournaments_left: Остаток турниров (None — безлимит).
    """

    monthly_tournaments_limit: int | None
    tournaments_used: int
    tournaments_left: int | None


def get_current_period(today: date | None = None) -> tuple[int, int]:
    """Возвращает текущий учётный период (год, месяц).

    Args:
        today: Дата для расчета периода. Если не передана, используется текущая дата.

    Returns:
        tuple[int, int]: Пара (год, месяц).
    """
    current = today or timezone.localdate()
    return current.year, current.month


def _get_or_create_period_usage(
    member: ClubMember,
    plan: ClubPlayerPlan,
    *,
    year: int,
    month: int,
    select_for_update: bool = False,
) -> ClubPlanSlotUsage:
    """Возвращает usage за период и синхронизирует его с текущим тарифом.

    В БД запись usage уникальна по участнику и месяцу. Если участник сменил тариф
    внутри месяца, старый usage уже должен быть конвертирован в переносимый остаток,
    а базовый счётчик нового тарифа начинается заново.
    """
    queryset = ClubPlanSlotUsage.objects
    if select_for_update:
        queryset = queryset.select_for_update()

    usage, _ = queryset.get_or_create(
        club_member=member,
        period_year=year,
        period_month=month,
        defaults={
            "plan": plan,
            "tournaments_used": 0,
        },
    )
    if usage.plan_id != plan.id:
        usage.plan = plan
        usage.tournaments_used = 0
        usage.save(update_fields=["plan", "tournaments_used", "updated_at"])
    return cast(ClubPlanSlotUsage, usage)


def _plan_has_unlimited_registrations(plan: ClubPlayerPlan) -> bool:
    """Проверяет, есть ли у тарифа безлимитные регистрации."""
    return bool(
        plan.has_unlimited_registrations or plan.max_tournaments_per_month is None
    )


def _get_plan_registration_limit(plan: ClubPlayerPlan) -> int | None:
    """Возвращает лимит регистраций тарифа или None для безлимита."""
    if _plan_has_unlimited_registrations(plan):
        return None
    max_tournaments_per_month = plan.max_tournaments_per_month
    if max_tournaments_per_month is None:
        return 0
    return int(max_tournaments_per_month)


def _get_plan_duration_days(plan: ClubPlayerPlan) -> int:
    """Возвращает длительность тарифа в днях."""
    return max(int(plan.duration_days), 1)


def _rebind_current_period_usage_to_plan(
    member: ClubMember,
    plan: ClubPlayerPlan,
) -> None:
    """Перепривязывает usage текущего месяца к новому тарифу и сбрасывает счётчик."""
    year, month = get_current_period()
    usage = (
        ClubPlanSlotUsage.objects.select_for_update()
        .filter(
            club_member=member,
            period_year=year,
            period_month=month,
        )
        .first()
    )
    if usage is None or usage.plan_id == plan.id:
        return
    usage.plan = plan
    usage.tournaments_used = 0
    usage.save(update_fields=["plan", "tournaments_used", "updated_at"])


def get_member_active_plan(member: ClubMember) -> ClubMemberPlan | None:
    """Возвращает активный тариф участника клуба.

    Args:
        member: Участник клуба.

    Returns:
        ClubMemberPlan | None: Активное назначение тарифа или None.
    """
    plan = (
        ClubMemberPlan.objects.select_related("plan")
        .filter(
            club_member=member,
            status=ClubMemberPlanStatus.ACTIVE,
            plan__is_active=True,
        )
        .filter(models.Q(ended_at__isnull=True) | models.Q(ended_at__gt=timezone.now()))
        .order_by("-started_at")
        .first()
    )
    return cast(ClubMemberPlan | None, plan)


def _get_plan_period_extension_base(
    now,
    current_plan: ClubMemberPlan | None,
):
    """Возвращает базовую дату для продления периода тарифа."""
    if current_plan and current_plan.ended_at and current_plan.ended_at > now:
        return current_plan.ended_at
    return now


def _get_transferable_tournaments_balance(
    member_plan: ClubMemberPlan,
) -> int:
    """Возвращает переносимый остаток слотов активного тарифа."""
    base_limit = _get_plan_registration_limit(member_plan.plan)
    if base_limit is None:
        return 0
    normalized_base_limit = int(base_limit)

    year, month = _get_usage_period_for_member_plan(member_plan)
    usage = (
        ClubPlanSlotUsage.objects.select_for_update()
        .filter(
            club_member=member_plan.club_member,
            plan=member_plan.plan,
            period_year=year,
            period_month=month,
        )
        .first()
    )
    tournaments_used = int(usage.tournaments_used) if usage else 0
    base_remaining = max(normalized_base_limit - tournaments_used, 0)
    return base_remaining + int(member_plan.bonus_tournaments_balance)


def _get_usage_period_for_member_plan(
    member_plan: ClubMemberPlan,
    *,
    today: date | None = None,
) -> tuple[int, int]:
    """Возвращает период учёта usage в зависимости от режима лимита тарифа."""
    if (
        member_plan.plan.registration_limit_period
        == ClubRegistrationLimitPeriod.PLAN_PERIOD
    ):
        started_local = timezone.localtime(member_plan.started_at).date()
        return started_local.year, started_local.month
    return get_current_period(today=today)


def assign_member_plan(
    member: ClubMember,
    plan: ClubPlayerPlan,
    *,
    assigned_by: AbstractUser | None = None,
    change_reason: str = "",
) -> ClubMemberPlan:
    """Назначает участнику новый активный тариф.

    Args:
        member: Участник клуба.
        plan: Тариф клуба.
        assigned_by: Пользователь, выполнивший назначение.
        change_reason: Причина изменения тарифа.

    Returns:
        ClubMemberPlan: Новое активное назначение тарифа.

    Raises:
        ValueError: Если тариф принадлежит другому клубу.
    """
    if plan.club_id != member.club_id:
        raise ValueError("Нельзя назначить тариф другого клуба.")

    with transaction.atomic():
        now = timezone.now()
        current_plan = (
            ClubMemberPlan.objects.select_for_update()
            .select_related("plan")
            .filter(
                club_member=member,
                status=ClubMemberPlanStatus.ACTIVE,
            )
            .filter(models.Q(ended_at__isnull=True) | models.Q(ended_at__gt=now))
            .order_by("-started_at")
            .first()
        )
        carried_balance = (
            _get_transferable_tournaments_balance(current_plan)
            if current_plan is not None
            else 0
        )
        end_base = _get_plan_period_extension_base(now, current_plan)

        ClubMemberPlan.objects.select_for_update().filter(
            club_member=member,
            status=ClubMemberPlanStatus.ACTIVE,
        ).update(
            status=ClubMemberPlanStatus.ENDED,
            ended_at=now,
        )
        created_plan = ClubMemberPlan.objects.create(
            club_member=member,
            plan=plan,
            status=ClubMemberPlanStatus.ACTIVE,
            ended_at=_club_plan_period_end(plan, end_base),
            bonus_tournaments_balance=carried_balance,
            auto_renew=False,
            assigned_by=assigned_by,
            change_reason=change_reason,
        )
        _rebind_current_period_usage_to_plan(member, plan)
        return cast(ClubMemberPlan, created_plan)


def _club_plan_period_end(plan: ClubPlayerPlan, base=None):
    """Возвращает дату окончания клубного тарифного периода."""
    return (base or timezone.now()) + timedelta(days=_get_plan_duration_days(plan))


def purchase_member_plan(
    member: ClubMember,
    plan: ClubPlayerPlan,
    *,
    assigned_by: AbstractUser | None = None,
    change_reason: str = "",
    auto_renew: bool = False,
) -> ClubMemberPlan:
    """Активирует или продлевает оплаченный клубный тариф участника."""
    if plan.club_id != member.club_id:
        raise ValueError("Нельзя активировать тариф другого клуба.")

    with transaction.atomic():
        now = timezone.now()
        current_plan = (
            ClubMemberPlan.objects.select_for_update()
            .select_related("plan")
            .filter(
                club_member=member,
                status=ClubMemberPlanStatus.ACTIVE,
            )
            .filter(models.Q(ended_at__isnull=True) | models.Q(ended_at__gt=now))
            .order_by("-started_at")
            .first()
        )

        if current_plan is not None and current_plan.plan_id == plan.id:
            base = _get_plan_period_extension_base(now, current_plan)
            current_plan.ended_at = _club_plan_period_end(plan, base)
            plan_limit = _get_plan_registration_limit(plan)
            if plan_limit is not None:
                current_plan.bonus_tournaments_balance += plan_limit
            current_plan.auto_renew = auto_renew
            current_plan.assigned_by = assigned_by
            current_plan.change_reason = change_reason
            current_plan.save(
                update_fields=[
                    "ended_at",
                    "bonus_tournaments_balance",
                    "auto_renew",
                    "assigned_by",
                    "change_reason",
                ]
            )
            return cast(ClubMemberPlan, current_plan)

        carried_balance = (
            _get_transferable_tournaments_balance(current_plan)
            if current_plan is not None
            else 0
        )
        end_base = _get_plan_period_extension_base(now, current_plan)

        ClubMemberPlan.objects.filter(
            club_member=member,
            status=ClubMemberPlanStatus.ACTIVE,
        ).update(
            status=ClubMemberPlanStatus.ENDED,
            ended_at=now,
        )

        created_plan = ClubMemberPlan.objects.create(
            club_member=member,
            plan=plan,
            status=ClubMemberPlanStatus.ACTIVE,
            assigned_by=assigned_by,
            change_reason=change_reason,
            ended_at=_club_plan_period_end(plan, end_base),
            bonus_tournaments_balance=carried_balance,
            auto_renew=auto_renew,
        )
        _rebind_current_period_usage_to_plan(member, plan)
        return cast(ClubMemberPlan, created_plan)


def cancel_member_plan_auto_renew(member: ClubMember) -> ClubMemberPlan | None:
    """Отключает автопродление активного клубного тарифа участника."""
    member_plan = get_member_active_plan(member)
    if member_plan is None or member_plan.ended_at is None:
        return None
    member_plan.auto_renew = False
    member_plan.save(update_fields=["auto_renew"])
    return member_plan


def enable_member_plan_auto_renew(member: ClubMember) -> ClubMemberPlan | None:
    """Включает автопродление активного клубного тарифа участника."""
    member_plan = get_member_active_plan(member)
    if member_plan is None or member_plan.ended_at is None:
        return None
    member_plan.auto_renew = True
    member_plan.save(update_fields=["auto_renew"])
    return member_plan


def get_member_plan_limits(
    member: ClubMember,
    *,
    today: date | None = None,
) -> MemberPlanLimits | None:
    """Возвращает лимиты и остатки по тарифу участника в текущем периоде.

    Args:
        member: Участник клуба.
        today: Дата для расчета периода.

    Returns:
        MemberPlanLimits | None: Снимок лимитов или None, если тариф не назначен.
    """
    if not member.club.use_player_plans:
        return None

    member_plan = get_member_active_plan(member)
    if not member_plan:
        return None

    plan = member_plan.plan
    year, month = _get_usage_period_for_member_plan(member_plan, today=today)
    usage = _get_or_create_period_usage(
        member,
        plan,
        year=year,
        month=month,
    )

    monthly_tournaments_limit = _get_plan_registration_limit(plan)
    bonus_balance = int(member_plan.bonus_tournaments_balance)
    effective_limit: int | None
    tournaments_left: int | None
    if monthly_tournaments_limit is None:
        effective_limit = None
        tournaments_left = None
        tournaments_used = usage.tournaments_used
    else:
        effective_limit_int = monthly_tournaments_limit + bonus_balance
        tournaments_left_int = (
            max(monthly_tournaments_limit - usage.tournaments_used, 0) + bonus_balance
        )
        effective_limit = effective_limit_int
        tournaments_left = tournaments_left_int
        tournaments_used = max(effective_limit_int - tournaments_left_int, 0)

    return MemberPlanLimits(
        monthly_tournaments_limit=effective_limit,
        tournaments_used=tournaments_used,
        tournaments_left=tournaments_left,
    )


def _tournament_has_entry_fee(tournament: Tournament) -> bool:
    """Проверяет, установлен ли вступительный взнос для турнира."""
    fee = getattr(tournament, "entry_fee", None) or Decimal("0")
    return fee > Decimal("0")


def _member_has_available_limit(member: ClubMember) -> bool:
    """Проверяет, есть ли у участника доступный лимит турниров."""
    limits = get_member_plan_limits(member)
    if limits is None:
        return False
    # Безлимитный тариф
    if limits.monthly_tournaments_limit is None:
        return True
    # Есть остаток
    return limits.tournaments_left is not None and limits.tournaments_left > 0


def check_tournament_registration_eligibility(
    member: ClubMember,
    tournament: Tournament,
) -> RegistrationEligibility:
    """Проверяет возможность и режим регистрации участника на клубный турнир.

    Логика (аналогично глобальной платформе):

    **Однодневные турниры:**
    - Тариф не обязателен
    - Если есть взнос → PAID (оплата взноса)
    - Если нет взноса → FREE (бесплатно, лимит НЕ расходуется)

    **Многодневные турниры с взносом:**
    - Если есть тариф и лимит → FREE (бесплатно, лимит расходуется)
    - Если нет тарифа или лимит исчерпан → PAID (оплата взноса)

    **Многодневные турниры без взноса:**
    - Обязателен тариф с доступным лимитом
    - FREE (бесплатно, лимит расходуется)

    Args:
        member: Участник клуба.
        tournament: Турнир для проверки.

    Returns:
        RegistrationEligibility: Результат проверки.
    """
    # Турнир другого клуба — пропускаем проверку тарифов
    if tournament.club_id != member.club_id:
        return RegistrationEligibility(
            mode=RegistrationMode.FREE,
            message="",
            consumes_limit=False,
        )

    has_entry_fee = _tournament_has_entry_fee(tournament)

    if not member.club.use_player_plans:
        return RegistrationEligibility(
            mode=(RegistrationMode.PAID if has_entry_fee else RegistrationMode.FREE),
            message=(
                "Для участия необходимо оплатить вступительный взнос."
                if has_entry_fee
                else ""
            ),
            consumes_limit=False,
        )

    # Проверяем, использует ли клуб систему тарифов
    active_plans_qs = ClubPlayerPlan.objects.filter(
        club_id=member.club_id,
        is_active=True,
    )
    if not active_plans_qs.exists():
        # Клуб не использует тарифы — свободная регистрация
        return RegistrationEligibility(
            mode=RegistrationMode.FREE,
            message="",
            consumes_limit=False,
        )
    member_plan = get_member_active_plan(member)

    # Проверка доступа по тарифу (если настроены правила доступа)
    if member_plan:
        access_rules_qs = ClubPlanTournamentAccess.objects.filter(tournament=tournament)
        if access_rules_qs.exists():
            access = access_rules_qs.filter(
                plan=member_plan.plan, is_allowed=True
            ).exists()
            if not access:
                return RegistrationEligibility(
                    mode=RegistrationMode.BLOCKED,
                    message="Ваш тариф не дает доступ к этому турниру.",
                    consumes_limit=False,
                )

    # === ОДНОДНЕВНЫЕ ТУРНИРЫ ===
    if tournament.is_one_day:
        if has_entry_fee:
            # Однодневный с взносом → всегда оплата, лимит НЕ расходуется
            return RegistrationEligibility(
                mode=RegistrationMode.PAID,
                message="Для участия необходимо оплатить вступительный взнос.",
                consumes_limit=False,
            )
        else:
            # Однодневный без взноса → бесплатно, лимит НЕ расходуется
            return RegistrationEligibility(
                mode=RegistrationMode.FREE,
                message="",
                consumes_limit=False,
            )

    # === МНОГОДНЕВНЫЕ ТУРНИРЫ ===
    has_limit = _member_has_available_limit(member)

    if has_entry_fee:
        # Многодневный с взносом
        if has_limit:
            # Есть лимит → бесплатно по тарифу, лимит расходуется
            return RegistrationEligibility(
                mode=RegistrationMode.FREE,
                message="",
                consumes_limit=True,
            )
        else:
            # Нет лимита → оплата взноса
            return RegistrationEligibility(
                mode=RegistrationMode.PAID,
                message="Лимит турниров по тарифу исчерпан. "
                "Вы можете оплатить вступительный взнос.",
                consumes_limit=False,
            )
    else:
        # Многодневный без взноса — обязателен тариф с лимитом
        if not member_plan:
            return RegistrationEligibility(
                mode=RegistrationMode.BLOCKED,
                message="Для участия в турнирах клуба нужно выбрать тариф.",
                consumes_limit=False,
            )
        if not has_limit:
            return RegistrationEligibility(
                mode=RegistrationMode.BLOCKED,
                message="Лимит турниров по вашему тарифу на этот месяц исчерпан.",
                consumes_limit=False,
            )
        # Есть тариф и лимит → бесплатно, лимит расходуется
        return RegistrationEligibility(
            mode=RegistrationMode.FREE,
            message="",
            consumes_limit=True,
        )


def can_member_register_for_tournament(
    member: ClubMember,
    tournament: Tournament,
) -> tuple[bool, str]:
    """Проверяет возможность регистрации участника на клубный турнир по тарифу.

    Упрощённая обёртка для обратной совместимости.
    Возвращает True, если регистрация возможна (FREE или PAID).

    Args:
        member: Участник клуба.
        tournament: Турнир для проверки.

    Returns:
        tuple[bool, str]: Пара (можно_ли, сообщение_об_ошибке).
    """
    eligibility = check_tournament_registration_eligibility(member, tournament)
    if eligibility.mode == RegistrationMode.BLOCKED:
        return False, eligibility.message
    return True, ""


def consume_member_tournament_limit(
    member: ClubMember,
    tournament: Tournament,
) -> tuple[bool, str]:
    """Списывает лимиты участника при регистрации на турнир.

    Лимит расходуется ТОЛЬКО если:
    - Многодневный турнир с взносом и есть доступный лимит
    - Многодневный турнир без взноса

    Лимит НЕ расходуется:
    - Однодневные турниры (любые)
    - Многодневные с взносом при оплате взноса (лимит исчерпан)

    Args:
        member: Участник клуба.
        tournament: Турнир, на который выполняется регистрация.

    Returns:
        tuple[bool, str]: Пара (успех, сообщение).
    """
    eligibility = check_tournament_registration_eligibility(member, tournament)

    if eligibility.mode == RegistrationMode.BLOCKED:
        return False, eligibility.message

    # Лимит не нужно списывать
    if not eligibility.consumes_limit:
        return True, ""

    # Списываем лимит
    with transaction.atomic():
        member_plan = (
            ClubMemberPlan.objects.select_for_update()
            .select_related("plan")
            .filter(
                club_member=member,
                status=ClubMemberPlanStatus.ACTIVE,
                plan__is_active=True,
            )
            .first()
        )
        if not member_plan:
            return False, "Тариф не найден."

        plan = member_plan.plan
        year, month = _get_usage_period_for_member_plan(member_plan)
        usage = _get_or_create_period_usage(
            member,
            plan,
            year=year,
            month=month,
            select_for_update=True,
        )

        # Повторная проверка лимита внутри транзакции
        plan_limit = _get_plan_registration_limit(plan)
        if plan_limit is None:
            usage.tournaments_used += 1
            usage.save(update_fields=["tournaments_used", "updated_at"])
            return True, ""

        if usage.tournaments_used < plan_limit:
            usage.tournaments_used += 1
            usage.save(update_fields=["tournaments_used", "updated_at"])
            return True, ""

        if member_plan.bonus_tournaments_balance > 0:
            member_plan.bonus_tournaments_balance -= 1
            member_plan.save(
                update_fields=["bonus_tournaments_balance"],
            )
            return True, ""

        return False, "Лимит турниров по вашему тарифу на этот месяц исчерпан."

    return True, ""


def restore_member_tournament_limit(member: ClubMember) -> bool:
    """Возвращает один слот клубного тарифа в текущем периоде, если он был списан."""
    with transaction.atomic():
        member_plan = (
            ClubMemberPlan.objects.select_for_update()
            .select_related("plan")
            .filter(
                club_member=member,
                status=ClubMemberPlanStatus.ACTIVE,
                plan__is_active=True,
            )
            .first()
        )
        if not member_plan:
            return False

        year, month = _get_usage_period_for_member_plan(member_plan)
        usage = _get_or_create_period_usage(
            member,
            member_plan.plan,
            year=year,
            month=month,
            select_for_update=True,
        )
        plan_limit = _get_plan_registration_limit(member_plan.plan)
        if plan_limit is None:
            if usage.tournaments_used <= 0:
                return False
            usage.tournaments_used -= 1
            usage.save(update_fields=["tournaments_used", "updated_at"])
            return True

        if usage.tournaments_used > 0:
            usage.tournaments_used -= 1
            usage.save(update_fields=["tournaments_used", "updated_at"])
            return True

        member_plan.bonus_tournaments_balance += 1
        member_plan.save(update_fields=["bonus_tournaments_balance"])
        return True
