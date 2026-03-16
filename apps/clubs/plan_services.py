"""Сервисная логика клубных тарифов игроков (Phase 8)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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


@dataclass(slots=True, frozen=True)
class MemberPlanLimits:
    """Снимок лимитов участника по клубному тарифу.

    Attributes:
        monthly_tournaments_limit: Лимит турниров на месяц (None — без лимита).
        tournaments_used: Уже использовано турниров в текущем месяце.
        tournaments_left: Остаток турниров (None — безлимит).
        monthly_slots_limit: Лимит слотов в месяце с учетом переноса.
        slots_used: Уже использовано слотов.
        slots_left: Остаток слотов.
    """

    monthly_tournaments_limit: int | None
    tournaments_used: int
    tournaments_left: int | None
    monthly_slots_limit: int
    slots_used: int
    slots_left: int


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
        defaults={
            "tournaments_used": 0,
            "slots_used": 0,
            "rollover_in": 0,
            "rollover_out": 0,
        },
    )

    monthly_tournaments_limit = plan.max_tournaments_per_month
    tournaments_used = usage.tournaments_used
    tournaments_left: int | None
    if monthly_tournaments_limit is None:
        tournaments_left = None
    else:
        tournaments_left = max(monthly_tournaments_limit - tournaments_used, 0)

    monthly_slots_limit = plan.monthly_slots + usage.rollover_in
    slots_used = usage.slots_used
    slots_left = max(monthly_slots_limit - slots_used, 0)

    return MemberPlanLimits(
        monthly_tournaments_limit=monthly_tournaments_limit,
        tournaments_used=tournaments_used,
        tournaments_left=tournaments_left,
        monthly_slots_limit=monthly_slots_limit,
        slots_used=slots_used,
        slots_left=slots_left,
    )


def can_member_register_for_tournament(
    member: ClubMember,
    tournament: Tournament,
) -> tuple[bool, str]:
    """Проверяет возможность регистрации участника на клубный турнир по тарифу.

    Args:
        member: Участник клуба.
        tournament: Турнир для проверки.

    Returns:
        tuple[bool, str]: Пара (можно_ли, сообщение_об_ошибке).
    """
    if tournament.club_id != member.club_id:
        return True, ""

    active_plans_qs = ClubPlayerPlan.objects.filter(
        club_id=member.club_id,
        is_active=True,
    )
    if not active_plans_qs.exists():
        # Fallback: клуб не использует тарифы игроков.
        return True, ""

    member_plan = get_member_active_plan(member)
    if not member_plan:
        return False, "Для участия в турнирах клуба нужно выбрать тариф."

    access_rules_qs = ClubPlanTournamentAccess.objects.filter(tournament=tournament)
    if access_rules_qs.exists():
        access = access_rules_qs.filter(plan=member_plan.plan, is_allowed=True).exists()
        if not access:
            return False, "Ваш тариф не дает доступ к этому турниру."

    limits = get_member_plan_limits(member)
    if limits is None:
        return False, "Тариф участника не найден."

    if (
        limits.monthly_tournaments_limit is not None
        and limits.tournaments_used >= limits.monthly_tournaments_limit
    ):
        return False, "Лимит турниров по вашему тарифу на этот месяц исчерпан."

    required_slots = 0 if tournament.is_one_day else 1
    if required_slots > 0 and limits.slots_left < required_slots:
        return False, "Недостаточно слотов по вашему тарифу для участия в турнире."

    return True, ""


def consume_member_tournament_limit(
    member: ClubMember,
    tournament: Tournament,
) -> tuple[bool, str]:
    """Списывает лимиты участника при регистрации на турнир.

    Args:
        member: Участник клуба.
        tournament: Турнир, на который выполняется регистрация.

    Returns:
        tuple[bool, str]: Пара (успех, сообщение).
    """
    if tournament.club_id != member.club_id:
        return True, ""

    active_plans_qs = ClubPlayerPlan.objects.filter(
        club_id=member.club_id, is_active=True
    )
    if not active_plans_qs.exists():
        return True, ""

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
            return False, "Для участия в турнирах клуба нужно выбрать тариф."

        plan = member_plan.plan
        year, month = get_current_period()
        usage, _ = ClubPlanSlotUsage.objects.select_for_update().get_or_create(
            club_member=member,
            plan=plan,
            period_year=year,
            period_month=month,
            defaults={
                "tournaments_used": 0,
                "slots_used": 0,
                "rollover_in": 0,
                "rollover_out": 0,
            },
        )

        access_rules_qs = ClubPlanTournamentAccess.objects.filter(tournament=tournament)
        if access_rules_qs.exists():
            if not access_rules_qs.filter(plan=plan, is_allowed=True).exists():
                return False, "Ваш тариф не дает доступ к этому турниру."

        if (
            plan.max_tournaments_per_month is not None
            and usage.tournaments_used >= plan.max_tournaments_per_month
        ):
            return False, "Лимит турниров по вашему тарифу на этот месяц исчерпан."

        required_slots = 0 if tournament.is_one_day else 1
        available_slots = max(
            plan.monthly_slots + usage.rollover_in - usage.slots_used, 0
        )
        if required_slots > 0 and available_slots < required_slots:
            return False, "Недостаточно слотов по вашему тарифу для участия в турнире."

        # Внутри транзакции: сначала валидация, потом атомарное списание usage.
        usage.tournaments_used += 1
        if required_slots > 0:
            usage.slots_used += required_slots
        usage.save(update_fields=["tournaments_used", "slots_used", "updated_at"])

    return True, ""


def rollover_member_slots(
    member_plan: ClubMemberPlan,
    *,
    source_year: int,
    source_month: int,
    target_year: int,
    target_month: int,
) -> int:
    """Переносит остаток слотов участника из одного периода в следующий.

    Args:
        member_plan: Активное назначение тарифа участнику.
        source_year: Год исходного периода.
        source_month: Месяц исходного периода.
        target_year: Год целевого периода.
        target_month: Месяц целевого периода.

    Returns:
        int: Количество перенесенных слотов.
    """
    plan = member_plan.plan
    if not plan.allow_rollover_slots:
        return 0

    source_usage, _ = ClubPlanSlotUsage.objects.get_or_create(
        club_member=member_plan.club_member,
        plan=plan,
        period_year=source_year,
        period_month=source_month,
        defaults={
            "tournaments_used": 0,
            "slots_used": 0,
            "rollover_in": 0,
            "rollover_out": 0,
        },
    )
    available_slots = max(
        plan.monthly_slots + source_usage.rollover_in - source_usage.slots_used, 0
    )
    rollover: int = min(available_slots, plan.rollover_cap)

    target_usage, _ = ClubPlanSlotUsage.objects.get_or_create(
        club_member=member_plan.club_member,
        plan=plan,
        period_year=target_year,
        period_month=target_month,
        defaults={
            "tournaments_used": 0,
            "slots_used": 0,
            "rollover_in": 0,
            "rollover_out": 0,
        },
    )
    source_usage.rollover_out = rollover
    source_usage.save(update_fields=["rollover_out", "updated_at"])

    target_usage.rollover_in = rollover
    target_usage.save(update_fields=["rollover_in", "updated_at"])
    return rollover
