import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.decorators import login_required_with_message, require_filled_profile

from .models import SubscriptionTier, UserSubscription

logger = logging.getLogger(__name__)


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

    # Fetch all tiers
    tiers = list(SubscriptionTier.objects.all().order_by("price"))

    # If user is NOT from Moscow, try to find regional prices
    # We assume 'moscow' is the default price (stored in SubscriptionTier.price)
    # Any other city gets the regional price if available.
    if user_city != "moscow":
        from .models import RegionalTierPrice

        # Fetch all regional prices
        regional_prices = {
            rp.tier_id: rp.price for rp in RegionalTierPrice.objects.all()
        }

        for tier in tiers:
            if tier.id in regional_prices:
                tier.effective_price = regional_prices[tier.id]
                tier.is_regional_price = True
            else:
                tier.effective_price = tier.price
                tier.is_regional_price = False
    else:
        for tier in tiers:
            tier.effective_price = tier.price
            tier.is_regional_price = False

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
    sub.start_date = timezone.now()
    sub.end_date = sub.start_date  # Will be updated by save() logic
    sub.is_active = True
    sub.cancelled_at = None  # Сбрасываем отмену при покупке / возобновлении
    sub.tournaments_registered_count = 0
    sub.save()

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
