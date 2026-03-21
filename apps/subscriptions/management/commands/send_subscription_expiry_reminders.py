"""
Напоминания об истечении подписки за 3 дня и за 1 день.

Выбирает подписки, у которых end_date попадает в окно «через 3 дня» (71–73 ч)
и «через 1 день» (23–25 ч), и отправляет пользователям сообщение в Telegram
(пользовательский бот) с кнопкой «Продлить подписку».

Запуск: python manage.py send_subscription_expiry_reminders

Рекомендуется добавить в cron раз в день (например в 10:00):
  0 10 * * * cd /path && python manage.py send_subscription_expiry_reminders
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from apps.core.email_service import send_subscription_pre_debit_notification
from apps.payments.models import SavedPaymentMethod
from apps.subscriptions.models import UserSubscription
from apps.subscriptions.utils import get_subscription_renew_amount
from apps.telegram_bot import notifications as tg
from apps.telegram_bot import services as bot_services

logger = logging.getLogger(__name__)

# Окна: напоминание «за 3 дня» — end_date через 71–73 ч, «за 1 день» — через 23–25 ч
HOURS_LOW_3 = 71
HOURS_HIGH_3 = 73
HOURS_LOW_1 = 23
HOURS_HIGH_1 = 25


class Command(BaseCommand):
    help = "Отправить напоминания об истечении подписки за 3 и 1 день в Telegram пользователям."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Не отправлять сообщения, только вывести подписки.",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        if not dry_run and not bot_services.is_configured():
            self.stdout.write(
                "Telegram user bot не настроен (TELEGRAM_USER_BOT_TOKEN)."
            )
            return

        now = timezone.now()

        # Окно «через 3 дня»: end_date в [now+71h, now+73h]
        low_3 = now + timedelta(hours=HOURS_LOW_3)
        high_3 = now + timedelta(hours=HOURS_HIGH_3)
        subscriptions_3d = list(
            UserSubscription.objects.filter(
                end_date__gte=low_3,
                end_date__lte=high_3,
                is_active=True,
                cancelled_at__isnull=True,  # Не отправляем для отменённых подписок
            )
            .select_related("user", "tier")
            .filter(user__isnull=False)
        )

        # Окно «через 1 день»: end_date в [now+23h, now+25h]
        low_1 = now + timedelta(hours=HOURS_LOW_1)
        high_1 = now + timedelta(hours=HOURS_HIGH_1)
        subscriptions_1d = list(
            UserSubscription.objects.filter(
                end_date__gte=low_1,
                end_date__lte=high_1,
                is_active=True,
                cancelled_at__isnull=True,  # Не отправляем для отменённых подписок
            )
            .select_related("user", "tier")
            .filter(user__isnull=False)
        )

        sent_3 = 0
        sent_1 = 0

        for subscription in subscriptions_3d:
            if dry_run:
                self.stdout.write(
                    f"  [3d] Подписка #{subscription.pk} пользователь {subscription.user} "
                    f"тариф {subscription.tier} истекает {subscription.end_date}"
                )
            else:
                try:
                    tg.notify_subscription_expiring(
                        subscription.user, subscription, days_left=3
                    )
                    sent_3 += 1
                except Exception as e:
                    logger.exception(
                        "send_subscription_expiry_reminder 3d subscription %s: %s",
                        subscription.pk,
                        e,
                    )

        for subscription in subscriptions_1d:
            if dry_run:
                self.stdout.write(
                    f"  [1d] Подписка #{subscription.pk} пользователь {subscription.user} "
                    f"тариф {subscription.tier} истекает {subscription.end_date}"
                )
            else:
                try:
                    has_autopay = SavedPaymentMethod.objects.filter(
                        user=subscription.user,
                        club__isnull=True,
                        is_active=True,
                        is_default_for_subscriptions=True,
                    ).exists()
                    renew_amount = (
                        str(int(get_subscription_renew_amount(subscription)))
                        if has_autopay
                        else None
                    )
                    tg.notify_subscription_expiring(
                        subscription.user,
                        subscription,
                        days_left=1,
                        has_autopay=has_autopay,
                        renew_amount=renew_amount,
                    )
                    if has_autopay and renew_amount:
                        site_base = (
                            getattr(
                                settings,
                                "TELEGRAM_BOT_SITE_BASE_URL",
                                "",
                            ).strip()
                            or "https://tennisfan.ru"
                        )
                        site_base = site_base.rstrip("/")
                        player = getattr(subscription.user, "player", None)
                        profile_url = (
                            f"{site_base}{reverse('profile', kwargs={'pk': player.pk})}"
                            if player
                            else f"{site_base}/"
                        )
                        send_subscription_pre_debit_notification(
                            subscription.user,
                            amount_rub=renew_amount,
                            tier_name=subscription.tier.get_name_display(),
                            end_date_str=subscription.end_date.strftime("%d.%m.%Y"),
                            profile_url=profile_url,
                        )
                    sent_1 += 1
                except Exception as e:
                    logger.exception(
                        "send_subscription_expiry_reminder 1d subscription %s: %s",
                        subscription.pk,
                        e,
                    )

        if dry_run:
            self.stdout.write(
                f"Dry-run: подписок за 3 дня: {len(subscriptions_3d)}, за 1 день: {len(subscriptions_1d)}"
            )
        else:
            self.stdout.write(
                f"Напоминаний отправлено: за 3 дня — {sent_3}, за 1 день — {sent_1}."
            )
