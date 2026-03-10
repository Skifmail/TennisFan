import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.decorators import login_required_with_message, require_filled_profile

from .models import SubscriptionTier, UserSubscription
from .utils import normalize_city_for_pricing

logger = logging.getLogger(__name__)


def _mark_user_paid_subscription(user):
    """Отметить, что пользователь хотя бы раз оплатил подписку (для акции «первая за 1 ₽»)."""
    try:
        player = user.player
        if not player.has_ever_paid_subscription:
            player.has_ever_paid_subscription = True
            player.save(update_fields=["has_ever_paid_subscription"])
    except Exception:
        pass


@login_required_with_message(
    "Информация о тарифах доступна только для зарегистрированных пользователей."
)
def pricing_page(request):
    """Страница тарифов — только для авторизованных пользователей."""

    # Logic to determine user city and apply regional pricing
    current_tier_id = None
    user_city = "moscow"  # Default to Moscow logic if not authenticated or not set
    if request.user.is_authenticated:
        try:
            if (
                hasattr(request.user, "subscription")
                and request.user.subscription.is_valid()
            ):
                current_tier_id = request.user.subscription.tier.id
        except UserSubscription.DoesNotExist:
            pass

        try:
            player = request.user.player
            if player.city:
                # Normalize city check. In our system Moscow is "moscow" in City choices
                # But let's check broadly.
                # City choices: MOSCOW = "moscow", SPB = "spb"
                city_val = player.city.lower().strip()
                if city_val in ["moscow", "moskva", "москва"]:
                    user_city = "moscow"
                else:
                    user_city = city_val
        except Exception:
            pass

    # Показываем на странице только тарифы, отмеченные для публикации.
    tiers = list(
        SubscriptionTier.objects.filter(is_visible=True).order_by(
            "sort_order", "price", "id"
        )
    )

    # If user is NOT from Moscow, try to find regional prices
    # We assume 'moscow' is the default price (stored in SubscriptionTier.price)
    # Any other city gets the regional price if available.
    if user_city != "moscow":
        from .models import RegionalTierPrice

        regional_by_tier = {
            rp.tier_id: rp for rp in RegionalTierPrice.objects.select_related("tier")
        }

        for tier in tiers:
            rp = regional_by_tier.get(tier.id)
            if rp:
                tier.effective_price = rp.price
                tier.is_regional_price = True
                tier.effective_original_price = (
                    rp.original_price
                    if rp.original_price is not None
                    else tier.original_price
                )
                tier.effective_original_price_ends_at = (
                    rp.original_price_ends_at
                    if rp.original_price_ends_at is not None
                    else tier.original_price_ends_at
                )
            else:
                tier.effective_price = tier.price
                tier.is_regional_price = False
                tier.effective_original_price = tier.original_price
                tier.effective_original_price_ends_at = tier.original_price_ends_at
    else:
        for tier in tiers:
            tier.effective_price = tier.price
            tier.is_regional_price = False
            tier.effective_original_price = tier.original_price
            tier.effective_original_price_ends_at = tier.original_price_ends_at

    now = timezone.now()
    user_has_ever_paid_subscription = False
    if request.user.is_authenticated:
        try:
            if getattr(request.user, "player", None):
                user_has_ever_paid_subscription = (
                    request.user.player.has_ever_paid_subscription
                )
        except Exception:
            pass

    for tier in tiers:
        orig = tier.effective_original_price
        ends_at = tier.effective_original_price_ends_at
        tier.show_promo_price = (
            orig is not None
            and orig > tier.effective_price
            and (ends_at is None or ends_at > now)
        )
        tier.show_first_one_ruble = (
            tier.first_subscription_one_ruble and not user_has_ever_paid_subscription
        )

    return render(
        request,
        "subscriptions/pricing.html",
        {
            "tiers": tiers,
            "current_tier_id": current_tier_id,
            "user_city": user_city,
        },
    )


@login_required
@require_filled_profile
def buy_subscription(request, tier_id):
    """Покупка подписки (или мгновенная активация — пока без платёжного шлюза)."""
    tier = get_object_or_404(SubscriptionTier, pk=tier_id)

    sub, created = UserSubscription.objects.get_or_create(
        user=request.user,
        defaults={"tier": tier, "end_date": timezone.now()},
    )

    sub.tier = tier
    sub.is_active = True
    sub.cancelled_at = None
    now = timezone.now()
    if created:
        sub.start_date = now
        sub.end_date = tier.apply_duration(now)
    else:
        # Продление: прибавляем срок выбранного тарифа от даты окончания
        # (или от текущего момента, если подписка уже истекла).
        base = sub.end_date if sub.end_date and sub.end_date > now else now
        sub.end_date = tier.apply_duration(base)
    city = getattr(request.user, "player", None) and getattr(
        request.user.player, "city", None
    )
    sub.purchase_city = normalize_city_for_pricing(city or "")
    sub.save()
    if not tier.is_unlimited and tier.max_tournaments > 0:
        sub.add_tournament_registration_slots(tier.max_tournaments)

    _mark_user_paid_subscription(request.user)

    try:
        from apps.payments.models import PaymentRecord
        from apps.subscriptions.utils import get_subscription_renew_amount

        PaymentRecord.objects.create(
            user=request.user,
            payment_type=PaymentRecord.PaymentType.SUBSCRIPTION,
            item_id=str(tier.pk),
            item_label=tier.get_name_display(),
            amount=get_subscription_renew_amount(sub),
            status="manual_success",
            is_recurring=False,
            autopay_enabled=False,
            metadata={"source": "legacy_buy_subscription"},
        )
    except Exception as exc:
        logger.warning("PaymentRecord create failed in buy_subscription: %s", exc)

    from apps.core.telegram_notify import notify_subscription_purchase

    notify_subscription_purchase(request.user, tier)

    messages.success(
        request, f"Вы успешно подписались на тариф {tier.get_name_display()}!"
    )
    return redirect("profile", pk=request.user.player.pk)


@login_required
@require_POST
def cancel_subscription(request):
    """
    Отмена подписки.

    Устанавливает ``cancelled_at``, но **не** снимает ``is_active``.
    Подписка остаётся действующей (``is_valid() == True``) до ``end_date``,
    после чего истекает естественным образом и не продлевается.
    """
    try:
        sub = request.user.subscription
    except UserSubscription.DoesNotExist:
        messages.warning(request, "У вас нет активной подписки.")
        return redirect("pricing")

    if sub.is_cancelled:
        messages.info(
            request,
            "Подписка уже отменена. Доступ сохраняется "
            f"до {sub.end_date.strftime('%d.%m.%Y')}.",
        )
        return redirect("profile", pk=request.user.player.pk)

    if not sub.is_valid():
        messages.info(request, "Подписка уже истекла.")
        return redirect("profile", pk=request.user.player.pk)

    sub.cancelled_at = timezone.now()
    sub.save(update_fields=["cancelled_at"])

    logger.info(
        "Subscription cancelled: user=%s, tier=%s, end_date=%s",
        request.user,
        sub.tier,
        sub.end_date,
    )

    messages.success(
        request,
        f"Подписка «{sub.tier.get_name_display()}» отменена. "
        f"Доступ к функциям сохраняется до {sub.end_date.strftime('%d.%m.%Y')}.",
    )
    return redirect("profile", pk=request.user.player.pk)
