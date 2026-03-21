from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.clubs.models import ClubMemberPlan, ClubMemberPlanStatus, ClubMembershipFee
from apps.clubs.payment_utils import decrypt_secret
from apps.clubs.plan_services import purchase_member_plan
from apps.payments.models import PaymentRecord, SavedPaymentMethod
from apps.payments.yookassa_client import create_recurring_payment_with_credentials

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Запуск автосписаний за продление клубных тарифов игроков."""

    help = (
        "Создаёт автоплатежи в ЮKassa для продления клубных тарифов игроков, "
        "которые включили автопродление и у которых сегодня истекает период."
    )

    def add_arguments(self, parser) -> None:
        """Добавить CLI-параметры команды."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только вывести, какие клубные тарифы попали бы под автосписание.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Основная точка входа команды автопродления клубных тарифов."""
        dry_run: bool = bool(options.get("dry_run"))

        now = timezone.now()
        target_date: date = now.date()
        member_plans = (
            ClubMemberPlan.objects.filter(
                status=ClubMemberPlanStatus.ACTIVE,
                auto_renew=True,
                ended_at__date=target_date,
            )
            .select_related("club_member__user", "club_member__club", "plan")
            .order_by("pk")
        )

        processed = 0
        succeeded = 0
        skipped_no_method = 0

        for member_plan in member_plans:
            user = member_plan.club_member.user
            payment_method = (
                SavedPaymentMethod.objects.filter(
                    user=user,
                    club=member_plan.club_member.club,
                    is_active=True,
                    is_default_for_club_plans=True,
                )
                .order_by("-created_at")
                .first()
            )

            if payment_method is None:
                skipped_no_method += 1
                logger.info(
                    "Skip recurring club plan payment: member_plan=%s user=%s reason=no_saved_method",
                    member_plan.pk,
                    user.pk,
                )
                continue

            payment_settings = (
                ClubMembershipFee.objects.filter(
                    club=member_plan.club_member.club,
                    payment_provider=ClubMembershipFee.PaymentProvider.YOOKASSA,
                )
                .exclude(payment_shop_id="")
                .exclude(payment_api_key="")
                .order_by("-id")
                .first()
            )
            if payment_settings is None:
                logger.info(
                    "Skip recurring club plan payment: member_plan=%s user=%s reason=no_club_payment_settings",
                    member_plan.pk,
                    user.pk,
                )
                continue

            try:
                secret = decrypt_secret(payment_settings.payment_api_key)
            except Exception as exc:
                logger.warning(
                    "Skip recurring club plan payment: member_plan=%s user=%s reason=secret_decrypt_failed error=%s",
                    member_plan.pk,
                    user.pk,
                    exc,
                )
                continue

            amount = member_plan.plan.monthly_fee
            amount_str = f"{amount:.2f}"
            description = (
                f"Автопродление клубного тарифа {member_plan.club_member.club.name}: "
                f"{member_plan.plan.name}"
            )

            if dry_run:
                self.stdout.write(
                    f"[DRY-RUN] Клубный тариф #{member_plan.pk} пользователя {user} "
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
                        "club_member_plan_id": member_plan.pk,
                        "user_id": user.pk,
                        "club_id": member_plan.club_member.club_id,
                        "club_plan_id": member_plan.plan_id,
                        "autopay": "1",
                    },
                )
                processed += 1

                if status == "succeeded":
                    PaymentRecord.objects.update_or_create(
                        user=user,
                        yookassa_payment_id=payment_id,
                        defaults={
                            "payment_type": PaymentRecord.PaymentType.CLUB_PLAN,
                            "item_id": str(member_plan.plan_id),
                            "item_label": (
                                f"{member_plan.club_member.club.name}: "
                                f"{member_plan.plan.name}"
                            ),
                            "amount": amount,
                            "status": status,
                            "is_recurring": True,
                            "autopay_enabled": True,
                            "metadata": {
                                "club_member_plan_id": member_plan.pk,
                                "payment_method_id": payment_method.payment_method_id,
                            },
                        },
                    )
                    purchase_member_plan(
                        member_plan.club_member,
                        member_plan.plan,
                        assigned_by=user,
                        change_reason="Автопродление клубного тарифа",
                        auto_renew=True,
                    )
                    succeeded += 1
                else:
                    logger.warning(
                        "Recurring club plan payment not succeeded immediately: "
                        "payment_id=%s status=%s member_plan=%s user=%s",
                        payment_id,
                        status,
                        member_plan.pk,
                        user.pk,
                    )
            except Exception as exc:
                logger.exception(
                    "Error during recurring club plan payment for member_plan=%s user=%s: %s",
                    member_plan.pk,
                    user.pk,
                    exc,
                )

        summary = (
            f"Автосписания клубных тарифов: обработано={processed}, "
            f"успешных={succeeded}, без карты={skipped_no_method}."
        )
        self.stdout.write(summary)
        logger.info(summary)
