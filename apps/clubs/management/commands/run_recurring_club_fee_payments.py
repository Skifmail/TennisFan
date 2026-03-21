from __future__ import annotations

import logging
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.clubs.models import ClubMember, ClubMemberStatus
from apps.clubs.payment_utils import decrypt_secret
from apps.clubs.services import get_current_period_label
from apps.clubs.views.helpers import _get_club_payment_settings
from apps.payments.models import SavedPaymentMethod
from apps.payments.yookassa_client import create_recurring_payment_with_credentials

from ...models import ClubFeePayment, ClubMembershipFee, FeePaymentMethod

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
                    skipped += 1
                    continue

                amount_str = f"{fee.amount:.2f}"
                description = f"Членский взнос {fee.club.name}, {period_label}"

                if dry_run:
                    self.stdout.write(
                        f"[DRY-RUN] Взнос клуба #{fee.club_id} участника {member.user_id} "
                        f"сумма {amount_str} ₽, метод {payment_method.payment_method_id}"
                    )
                    processed += 1
                    continue

                try:
                    payment_id, status = create_recurring_payment_with_credentials(
                        shop_id=payment_settings.payment_shop_id,
                        secret_key=secret,
                        amount=amount_str,
                        description=description,
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
                        succeeded += 1
                    else:
                        logger.warning(
                            "Recurring club fee payment not succeeded immediately: payment_id=%s status=%s club=%s member=%s",
                            payment_id,
                            status,
                            fee.club_id,
                            member.id,
                        )
                except Exception as exc:
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
