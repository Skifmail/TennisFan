from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.telegram_notify import notify_subscription_purchase
from apps.payments.models import PaymentRecord, SavedPaymentMethod
from apps.payments.yookassa_client import create_recurring_payment
from apps.subscriptions.models import UserSubscription
from apps.subscriptions.utils import (
    get_subscription_renew_amount,
    send_subscription_purchase_email,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Запуск автосписаний за продление подписок по сохранённым картам."""

    help = (
        "Создаёт автоплатежи в ЮKassa для продления подписок пользователей, "
        "которые согласились на автосписания и у которых сегодня истекает оплаченный период."
    )

    def add_arguments(self, parser) -> None:
        """Добавить CLI-параметры команды.

        Args:
            parser: Парсер аргументов Django management-команды.

        Returns:
            None: Аргументы добавляются к парсеру.
        """
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только вывести, какие подписки попали бы под автосписание, без реальных платежей.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Основная точка входа management-команды.

        Args:
            *args: Позиционные аргументы (не используются).
            **options: Именованные аргументы, включая ``dry-run``.

        Returns:
            None: Результат работы выводится в stdout и в лог.
        """
        dry_run: bool = bool(options.get("dry_run"))

        now = timezone.now()
        target_date: date = now.date()

        subscriptions = (
            UserSubscription.objects.filter(
                is_active=True,
                cancelled_at__isnull=True,
                end_date__date=target_date,
            )
            .select_related("user", "tier")
            .order_by("pk")
        )

        processed = 0
        succeeded = 0
        skipped_no_method = 0

        for sub in subscriptions:
            user = sub.user
            if user is None:
                continue

            payment_method = (
                SavedPaymentMethod.objects.filter(
                    user=user,
                    club__isnull=True,
                    is_active=True,
                    is_default_for_subscriptions=True,
                )
                .order_by("-created_at")
                .first()
            )

            if payment_method is None:
                skipped_no_method += 1
                logger.info(
                    "Skip recurring payment: subscription=%s user=%s reason=no_saved_method",
                    sub.pk,
                    user.pk,
                )
                continue

            amount = get_subscription_renew_amount(sub)
            amount_str = f"{amount:.2f}"
            description = (
                f"Автопродление подписки TennisFan: {sub.tier.get_name_display()}"
            )

            if dry_run:
                self.stdout.write(
                    f"[DRY-RUN] Подписка #{sub.pk} пользователя {user} "
                    f"тариф {sub.tier} сумма {amount_str} ₽, метод {payment_method.payment_method_id}"
                )
                processed += 1
                continue

            try:
                payment_id, status = create_recurring_payment(
                    amount=amount_str,
                    description=description,
                    payment_method_id=payment_method.payment_method_id,
                    metadata={
                        "subscription_id": sub.pk,
                        "user_id": user.pk,
                        "tier_id": sub.tier.pk,
                        "autopay": "1",
                    },
                )
                processed += 1
                logger.info(
                    "Recurring payment created: payment_id=%s status=%s subscription=%s user=%s",
                    payment_id,
                    status,
                    sub.pk,
                    user.pk,
                )

                if status == "succeeded":
                    PaymentRecord.objects.update_or_create(
                        user=user,
                        yookassa_payment_id=payment_id,
                        defaults={
                            "payment_type": PaymentRecord.PaymentType.SUBSCRIPTION,
                            "item_id": str(sub.tier_id),
                            "item_label": sub.tier.get_name_display(),
                            "amount": amount,
                            "status": status,
                            "is_recurring": True,
                            "autopay_enabled": True,
                            "metadata": {
                                "subscription_id": sub.pk,
                                "payment_method_id": payment_method.payment_method_id,
                            },
                        },
                    )
                    # Продлеваем подписку по тем же правилам, что и при ручной оплате.
                    now_local = timezone.now()
                    base = (
                        sub.end_date
                        if sub.end_date and sub.end_date > now_local
                        else now_local
                    )
                    sub.end_date = sub.tier.apply_duration(base)
                    sub.is_active = True
                    sub.cancelled_at = None
                    sub.save(update_fields=["end_date", "is_active", "cancelled_at"])

                    if not sub.tier.is_unlimited and sub.tier.fancoin_per_purchase > 0:
                        sub.add_fancoin(sub.tier.fancoin_per_purchase)

                    try:
                        notify_subscription_purchase(
                            user,
                            sub.tier,
                            amount_paid=amount_str,
                        )
                    except Exception as exc:
                        logger.warning(
                            "notify_subscription_purchase failed for recurring payment "
                            "subscription=%s user=%s: %s",
                            sub.pk,
                            user.pk,
                            exc,
                        )

                    try:
                        send_subscription_purchase_email(
                            user=user,
                            subscription=sub,
                            amount_paid=amount_str,
                        )
                    except Exception as exc:
                        logger.warning(
                            "send_subscription_purchase_email failed for recurring "
                            "payment subscription=%s user=%s: %s",
                            sub.pk,
                            user.pk,
                            exc,
                        )

                    succeeded += 1
                else:
                    logger.warning(
                        "Recurring payment not succeeded immediately: "
                        "payment_id=%s status=%s subscription=%s user=%s",
                        payment_id,
                        status,
                        sub.pk,
                        user.pk,
                    )
            except Exception as exc:
                logger.exception(
                    "Error during recurring payment for subscription=%s user=%s: %s",
                    sub.pk,
                    user.pk,
                    exc,
                )

        summary = (
            f"Автосписания: обработано подписок={processed}, "
            f"успешных списаний={succeeded}, без сохранённой карты={skipped_no_method}."
        )
        self.stdout.write(summary)
        logger.info(summary)
