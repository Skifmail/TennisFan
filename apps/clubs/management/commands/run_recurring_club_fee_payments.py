from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.clubs.finance_services import (
    calculate_balance_payment_breakdown,
    cancel_reserved_balance,
    confirm_reserved_balance,
    reserve_member_balance,
    spend_member_balance,
)
from apps.clubs.models import (
    ClubFeePayment,
    ClubMember,
    ClubMemberBalanceTransaction,
    ClubMembershipFee,
    ClubMemberStatus,
    FeePaymentMethod,
)
from apps.clubs.payment_utils import decrypt_secret
from apps.clubs.services import get_current_period_label
from apps.clubs.views.helpers import _get_club_payment_settings
from apps.payments.models import PaymentRecord, SavedPaymentMethod
from apps.payments.yookassa_client import create_recurring_payment_with_credentials

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Запускает автосписания по членским взносам клубов."""

    help = (
        "Создаёт автоплатежи в ЮKassa для членских взносов клубов у участников, "
        "которые сохранили карту для автосписаний и ещё не оплатили текущий период."
    )

    def add_arguments(self, parser) -> None:
        """Добавить CLI-параметры команды."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только вывести, какие взносы попали бы под автосписание.",
        )

    def _create_record(
        self,
        *,
        user,
        fee: ClubMembershipFee,
        period_label: str,
        payment_id: str,
        amount,
        status: str,
        balance_amount: str,
        external_amount: str,
        failure_reason: str = "",
        payment_method_id: str = "",
    ) -> None:
        """Сохранить детальную запись об автосписании взноса."""
        PaymentRecord.objects.update_or_create(
            user=user,
            yookassa_payment_id=payment_id,
            defaults={
                "payment_type": PaymentRecord.PaymentType.CLUB_FEE,
                "item_id": str(fee.id),
                "item_label": "Членский взнос клуба",
                "amount": amount,
                "status": status,
                "is_recurring": True,
                "autopay_enabled": True,
                "metadata": {
                    "club_id": fee.club_id,
                    "period_label": period_label,
                    "payment_method_id": payment_method_id,
                    "balance_amount": balance_amount,
                    "external_amount": external_amount,
                    "total_amount": f"{amount:.2f}",
                    "failure_reason": failure_reason,
                    "fee_payment_ref": payment_id if status == "succeeded" else "",
                },
            },
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Основная точка входа команды автосписаний по клубным взносам."""
        dry_run: bool = bool(options.get("dry_run"))
        processed = 0
        succeeded = 0
        skipped = 0

        fees = (
            ClubMembershipFee.objects.filter(
                is_active=True,
                payment_provider=ClubMembershipFee.PaymentProvider.YOOKASSA,
            )
            .exclude(payment_shop_id="")
            .exclude(payment_api_key="")
            .select_related("club")
            .order_by("club_id", "-id")
        )

        for fee in fees:
            period_label = get_current_period_label(fee)
            members = ClubMember.objects.filter(
                club=fee.club,
                status=ClubMemberStatus.ACTIVE,
            ).select_related("user")

            payment_settings = _get_club_payment_settings(fee.club)
            if payment_settings is None:
                timestamp = int(timezone.now().timestamp())
                for member in members:
                    payment_method = (
                        SavedPaymentMethod.objects.filter(
                            user=member.user,
                            club=fee.club,
                            is_active=True,
                            is_default_for_club_fees=True,
                        )
                        .order_by("-created_at")
                        .first()
                    )
                    if payment_method is None:
                        continue
                    self._create_record(
                        user=member.user,
                        fee=fee,
                        period_label=period_label,
                        payment_id=(
                            f"failed-club-fee-settings-{fee.pk}-{member.user_id}-{timestamp}"
                        ),
                        amount=fee.amount,
                        status="failed",
                        balance_amount="0.00",
                        external_amount=f"{fee.amount:.2f}",
                        failure_reason="no_club_payment_settings",
                        payment_method_id=payment_method.payment_method_id,
                    )
                logger.info(
                    "Skip recurring club fee payments: club=%s reason=no_club_payment_settings",
                    fee.club_id,
                )
                continue

            try:
                secret = decrypt_secret(payment_settings.payment_api_key)
            except Exception as exc:
                logger.warning(
                    "Skip recurring club fee payments: club=%s reason=secret_decrypt_failed error=%s",
                    fee.club_id,
                    exc,
                )
                continue

            for member in members:
                if ClubFeePayment.objects.filter(
                    member=member,
                    fee=fee,
                    period_label=period_label,
                ).exists():
                    continue

                breakdown = calculate_balance_payment_breakdown(member, fee.amount)
                payment_method = (
                    SavedPaymentMethod.objects.filter(
                        user=member.user,
                        club=fee.club,
                        is_active=True,
                        is_default_for_club_fees=True,
                    )
                    .order_by("-created_at")
                    .first()
                )

                if dry_run:
                    self.stdout.write(
                        f"[DRY-RUN] Взнос клуба #{fee.club_id} участника {member.user_id} "
                        f"сумма {fee.amount:.2f} ₽, баланс {breakdown.balance_to_apply:.2f} ₽, "
                        f"доплата {breakdown.external_amount_due:.2f} ₽"
                    )
                    processed += 1
                    continue

                if breakdown.external_amount_due <= 0:
                    spend_member_balance(
                        member,
                        breakdown.balance_to_apply,
                        source=ClubMemberBalanceTransaction.Source.CLUB_FEE_PAYMENT,
                        description=f"Автооплата взноса за период {period_label}",
                        reference=f"club-fee:{fee.id}:{period_label}:autopay",
                        metadata={"fee_id": fee.id, "period_label": period_label},
                    )
                    payment_ref = f"balance-club-fee-{member.id}-{int(timezone.now().timestamp())}"
                    ClubFeePayment.objects.create(
                        club=fee.club,
                        member=member,
                        fee=fee,
                        amount=fee.amount,
                        period_label=period_label,
                        method=FeePaymentMethod.BALANCE,
                        payment_ref=payment_ref,
                        paid_at=timezone.now(),
                    )
                    self._create_record(
                        user=member.user,
                        fee=fee,
                        period_label=period_label,
                        payment_id=payment_ref,
                        amount=fee.amount,
                        status="succeeded",
                        balance_amount=f"{fee.amount:.2f}",
                        external_amount="0.00",
                    )
                    processed += 1
                    succeeded += 1
                    continue

                if payment_method is None:
                    skipped += 1
                    self._create_record(
                        user=member.user,
                        fee=fee,
                        period_label=period_label,
                        payment_id=f"failed-club-fee-{member.id}-{int(timezone.now().timestamp())}",
                        amount=fee.amount,
                        status="failed",
                        balance_amount=f"{breakdown.balance_to_apply:.2f}",
                        external_amount=f"{breakdown.external_amount_due:.2f}",
                        failure_reason="no_saved_method",
                    )
                    continue

                balance_transaction = None
                if breakdown.balance_to_apply > 0:
                    try:
                        balance_transaction = reserve_member_balance(
                            member,
                            breakdown.balance_to_apply,
                            source=ClubMemberBalanceTransaction.Source.CLUB_FEE_PAYMENT,
                            description=f"Автооплата взноса за период {period_label}",
                            reference=f"club-fee:{fee.id}:{period_label}:autopay",
                            metadata={"fee_id": fee.id, "period_label": period_label},
                        )
                    except ValueError:
                        self._create_record(
                            user=member.user,
                            fee=fee,
                            period_label=period_label,
                            payment_id=f"failed-club-fee-balance-{member.id}-{int(timezone.now().timestamp())}",
                            amount=fee.amount,
                            status="failed",
                            balance_amount=f"{breakdown.balance_to_apply:.2f}",
                            external_amount=f"{breakdown.external_amount_due:.2f}",
                            failure_reason="balance_reserve_failed",
                        )
                        continue

                try:
                    payment_id, status = create_recurring_payment_with_credentials(
                        shop_id=payment_settings.payment_shop_id,
                        secret_key=secret,
                        amount=f"{breakdown.external_amount_due:.2f}",
                        description=f"Членский взнос {fee.club.name}, {period_label}",
                        payment_method_id=payment_method.payment_method_id,
                        metadata={
                            "payment_type": "club_fee",
                            "club_id": fee.club_id,
                            "fee_id": fee.id,
                            "member_id": member.id,
                            "period_label": period_label,
                            "autopay": "1",
                        },
                    )
                    processed += 1

                    if status == "succeeded":
                        confirm_reserved_balance(balance_transaction)
                        ClubFeePayment.objects.update_or_create(
                            payment_ref=payment_id,
                            defaults={
                                "club": fee.club,
                                "member": member,
                                "fee": fee,
                                "amount": fee.amount,
                                "period_label": period_label,
                                "method": FeePaymentMethod.ONLINE,
                                "paid_at": timezone.now(),
                            },
                        )
                        self._create_record(
                            user=member.user,
                            fee=fee,
                            period_label=period_label,
                            payment_id=payment_id,
                            amount=fee.amount,
                            status="succeeded",
                            balance_amount=f"{breakdown.balance_to_apply:.2f}",
                            external_amount=f"{breakdown.external_amount_due:.2f}",
                            payment_method_id=payment_method.payment_method_id,
                        )
                        succeeded += 1
                    else:
                        cancel_reserved_balance(balance_transaction)
                        self._create_record(
                            user=member.user,
                            fee=fee,
                            period_label=period_label,
                            payment_id=payment_id,
                            amount=fee.amount,
                            status=status,
                            balance_amount=f"{breakdown.balance_to_apply:.2f}",
                            external_amount=f"{breakdown.external_amount_due:.2f}",
                            payment_method_id=payment_method.payment_method_id,
                        )
                        logger.warning(
                            "Recurring club fee payment not succeeded immediately: payment_id=%s status=%s club=%s member=%s",
                            payment_id,
                            status,
                            fee.club_id,
                            member.id,
                        )
                except Exception as exc:
                    cancel_reserved_balance(balance_transaction)
                    self._create_record(
                        user=member.user,
                        fee=fee,
                        period_label=period_label,
                        payment_id=f"failed-club-fee-exc-{member.id}-{int(timezone.now().timestamp())}",
                        amount=fee.amount,
                        status="failed",
                        balance_amount=f"{breakdown.balance_to_apply:.2f}",
                        external_amount=f"{breakdown.external_amount_due:.2f}",
                        failure_reason=str(exc),
                        payment_method_id=payment_method.payment_method_id,
                    )
                    logger.exception(
                        "Error during recurring club fee payment for club=%s member=%s: %s",
                        fee.club_id,
                        member.id,
                        exc,
                    )

        summary = (
            f"Автосписания клубных взносов: обработано={processed}, "
            f"успешных={succeeded}, без карты={skipped}."
        )
        self.stdout.write(summary)
        logger.info(summary)
