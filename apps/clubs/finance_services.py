"""Сервисная логика баланса участника клуба."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import cast

from django.db import transaction
from django.utils import timezone

from .models import ClubMember, ClubMemberBalanceTransaction

MONEY_ZERO = Decimal("0.00")
ChoiceValue = str | tuple[str, str]


def _money(value: Decimal | int | str) -> Decimal:
    """Нормализует денежное значение до копеек."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _choice_value(value: ChoiceValue) -> str:
    """Нормализует значение TextChoices до строки."""
    if isinstance(value, tuple):
        return value[0]
    return value


@dataclass(slots=True, frozen=True)
class BalancePaymentBreakdown:
    """Разбиение оплаты на баланс и внешнюю доплату."""

    total_amount: Decimal
    balance_available: Decimal
    balance_to_apply: Decimal
    external_amount_due: Decimal


def get_member_balance(member: ClubMember) -> Decimal:
    """Возвращает доступный баланс участника клуба."""
    return _money(member.balance)


def calculate_balance_payment_breakdown(
    member: ClubMember | None,
    total_amount: Decimal | int | str,
) -> BalancePaymentBreakdown:
    """Считает, сколько можно покрыть балансом и сколько нужно доплатить."""
    normalized_total = _money(total_amount)
    if member is None or normalized_total <= MONEY_ZERO:
        return BalancePaymentBreakdown(
            total_amount=normalized_total,
            balance_available=MONEY_ZERO,
            balance_to_apply=MONEY_ZERO,
            external_amount_due=normalized_total,
        )

    balance_available = get_member_balance(member)
    balance_to_apply = min(balance_available, normalized_total)
    external_amount_due = normalized_total - balance_to_apply
    return BalancePaymentBreakdown(
        total_amount=normalized_total,
        balance_available=balance_available,
        balance_to_apply=balance_to_apply,
        external_amount_due=external_amount_due,
    )


def reserve_member_balance(
    member: ClubMember,
    amount: Decimal | int | str,
    *,
    source: ChoiceValue,
    description: str,
    reference: str = "",
    metadata: dict | None = None,
) -> ClubMemberBalanceTransaction | None:
    """Резервирует сумму баланса под внешнюю оплату."""
    normalized_amount = _money(amount)
    if normalized_amount <= MONEY_ZERO:
        return None

    with transaction.atomic():
        locked_member = ClubMember.objects.select_for_update().get(pk=member.pk)
        if _money(locked_member.balance) < normalized_amount:
            raise ValueError("Недостаточно средств на балансе.")

        locked_member.balance = _money(locked_member.balance) - normalized_amount
        locked_member.save(update_fields=["balance"])
        return cast(
            ClubMemberBalanceTransaction,
            ClubMemberBalanceTransaction.objects.create(
                club=locked_member.club,
                member=locked_member,
                direction=ClubMemberBalanceTransaction.Direction.DEBIT,
                source=_choice_value(source),
                status=ClubMemberBalanceTransaction.Status.PENDING,
                amount=normalized_amount,
                description=description,
                reference=reference,
                metadata=metadata or {},
            ),
        )


def confirm_reserved_balance(
    transaction_obj: ClubMemberBalanceTransaction | None,
) -> None:
    """Подтверждает ранее зарезервированное списание."""
    if transaction_obj is None:
        return

    if transaction_obj.status != ClubMemberBalanceTransaction.Status.PENDING:
        return

    transaction_obj.status = ClubMemberBalanceTransaction.Status.COMPLETED
    transaction_obj.completed_at = timezone.now()
    transaction_obj.save(update_fields=["status", "completed_at"])


def cancel_reserved_balance(
    transaction_obj: ClubMemberBalanceTransaction | None,
) -> None:
    """Отменяет резерв и возвращает деньги на баланс."""
    if transaction_obj is None:
        return

    with transaction.atomic():
        locked_tx = ClubMemberBalanceTransaction.objects.select_for_update().get(
            pk=transaction_obj.pk
        )
        if locked_tx.status != ClubMemberBalanceTransaction.Status.PENDING:
            return

        locked_member = ClubMember.objects.select_for_update().get(
            pk=locked_tx.member_id
        )
        locked_member.balance = _money(locked_member.balance) + _money(locked_tx.amount)
        locked_member.save(update_fields=["balance"])
        locked_tx.status = ClubMemberBalanceTransaction.Status.CANCELLED
        locked_tx.completed_at = timezone.now()
        locked_tx.save(update_fields=["status", "completed_at"])


def spend_member_balance(
    member: ClubMember,
    amount: Decimal | int | str,
    *,
    source: ChoiceValue,
    description: str,
    reference: str = "",
    metadata: dict | None = None,
) -> ClubMemberBalanceTransaction | None:
    """Списывает сумму с баланса сразу, без внешнего платежа."""
    transaction_obj = reserve_member_balance(
        member,
        amount,
        source=source,
        description=description,
        reference=reference,
        metadata=metadata,
    )
    confirm_reserved_balance(transaction_obj)
    return transaction_obj


def credit_member_balance(
    member: ClubMember,
    amount: Decimal | int | str,
    *,
    source: ChoiceValue,
    description: str,
    reference: str = "",
    metadata: dict | None = None,
) -> ClubMemberBalanceTransaction | None:
    """Зачисляет деньги на баланс участника клуба."""
    normalized_amount = _money(amount)
    if normalized_amount <= MONEY_ZERO:
        return None

    with transaction.atomic():
        locked_member = ClubMember.objects.select_for_update().get(pk=member.pk)
        locked_member.balance = _money(locked_member.balance) + normalized_amount
        locked_member.save(update_fields=["balance"])
        return cast(
            ClubMemberBalanceTransaction,
            ClubMemberBalanceTransaction.objects.create(
                club=locked_member.club,
                member=locked_member,
                direction=ClubMemberBalanceTransaction.Direction.CREDIT,
                source=_choice_value(source),
                status=ClubMemberBalanceTransaction.Status.COMPLETED,
                amount=normalized_amount,
                description=description,
                reference=reference,
                metadata=metadata or {},
                completed_at=timezone.now(),
            ),
        )


def admin_adjust_member_balance(
    member: ClubMember,
    *,
    operation: str,
    amount: Decimal | int | str,
    reason: str,
    adjusted_by,
) -> ClubMemberBalanceTransaction | None:
    """Корректирует баланс участника вручную с сохранением аудита."""
    normalized_amount = _money(amount)
    with transaction.atomic():
        locked_member = ClubMember.objects.select_for_update().get(pk=member.pk)
        previous_balance = _money(locked_member.balance)

        if operation == "set":
            target_balance = normalized_amount
            delta = target_balance - previous_balance
            if delta == MONEY_ZERO:
                return None
            direction = (
                ClubMemberBalanceTransaction.Direction.CREDIT
                if delta > MONEY_ZERO
                else ClubMemberBalanceTransaction.Direction.DEBIT
            )
            change_amount = abs(delta)
            new_balance = target_balance
        elif operation == "credit":
            direction = ClubMemberBalanceTransaction.Direction.CREDIT
            change_amount = normalized_amount
            new_balance = previous_balance + change_amount
        elif operation == "debit":
            direction = ClubMemberBalanceTransaction.Direction.DEBIT
            change_amount = normalized_amount
            if change_amount > previous_balance:
                raise ValueError("Нельзя списать больше доступного баланса.")
            new_balance = previous_balance - change_amount
        else:
            raise ValueError("Неизвестная операция корректировки баланса.")

        locked_member.balance = new_balance
        locked_member.save(update_fields=["balance"])
        return cast(
            ClubMemberBalanceTransaction,
            ClubMemberBalanceTransaction.objects.create(
                club=locked_member.club,
                member=locked_member,
                direction=direction,
                source=_choice_value(ClubMemberBalanceTransaction.Source.MANUAL),
                status=ClubMemberBalanceTransaction.Status.COMPLETED,
                amount=change_amount,
                description=reason,
                reference=f"manual-balance:{locked_member.pk}:{int(timezone.now().timestamp())}",
                metadata={
                    "operation": operation,
                    "adjusted_by_user_id": getattr(adjusted_by, "pk", None),
                    "adjusted_by_email": getattr(adjusted_by, "email", ""),
                    "previous_balance": f"{previous_balance:.2f}",
                    "new_balance": f"{new_balance:.2f}",
                },
                completed_at=timezone.now(),
            ),
        )
