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
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, cast

from django.db import transaction
from django.utils import timezone

from .models import (
    ClubMember,
    ClubMemberPlan,
    ClubMemberPlanStatus,
    ClubPlanSlotUsage,
    ClubPlanTournamentAccess,
    ClubPlayerPlan,
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
        .order_by("-started_at")
        .first()
    )
    return cast(ClubMemberPlan | None, plan)


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
        (
            ClubMemberPlan.objects.select_for_update()
            .filter(
                club_member=member,
                status=ClubMemberPlanStatus.ACTIVE,
            )
            .update(
                status=ClubMemberPlanStatus.ENDED,
                ended_at=now,
            )
        )
        return cast(
            ClubMemberPlan,
            ClubMemberPlan.objects.create(
                club_member=member,
                plan=plan,
                status=ClubMemberPlanStatus.ACTIVE,
                assigned_by=assigned_by,
                change_reason=change_reason,
            ),
        )


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
    member_plan = get_member_active_plan(member)
    if not member_plan:
        return None

    plan = member_plan.plan
    year, month = get_current_period(today=today)
    usage, _ = ClubPlanSlotUsage.objects.get_or_create(
        club_member=member,
        plan=plan,
        period_year=year,
        period_month=month,
        defaults={"tournaments_used": 0},
    )

    monthly_tournaments_limit = plan.max_tournaments_per_month
    tournaments_used = usage.tournaments_used
    tournaments_left: int | None
    if monthly_tournaments_limit is None:
        tournaments_left = None
    else:
        tournaments_left = max(monthly_tournaments_limit - tournaments_used, 0)

    return MemberPlanLimits(
        monthly_tournaments_limit=monthly_tournaments_limit,
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
    has_entry_fee = _tournament_has_entry_fee(tournament)

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
        year, month = get_current_period()
        usage, _ = ClubPlanSlotUsage.objects.select_for_update().get_or_create(
            club_member=member,
            plan=plan,
            period_year=year,
            period_month=month,
            defaults={"tournaments_used": 0},
        )

        # Повторная проверка лимита внутри транзакции
        if plan.max_tournaments_per_month is not None:
            if usage.tournaments_used >= plan.max_tournaments_per_month:
                return False, "Лимит турниров по вашему тарифу на этот месяц исчерпан."

        usage.tournaments_used += 1
        usage.save(update_fields=["tournaments_used", "updated_at"])

    return True, ""
