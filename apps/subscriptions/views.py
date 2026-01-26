from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .models import SubscriptionTier, UserSubscription

def pricing_page(request):
    tiers = SubscriptionTier.objects.exclude(name=SubscriptionTier.Level.FREE).order_by('price')
    
    current_tier_id = None
    if request.user.is_authenticated:
        try:
            # Check if user has an active subscription
            if hasattr(request.user, 'subscription') and request.user.subscription.is_valid():
                current_tier_id = request.user.subscription.tier.id
        except UserSubscription.DoesNotExist:
            pass
            
    return render(request, 'subscriptions/pricing.html', {
        'tiers': tiers,
        'current_tier_id': current_tier_id
    })

@login_required
def buy_subscription(request, tier_id):
    tier = get_object_or_404(SubscriptionTier, pk=tier_id)
    
    # Logic to process payment would go here.
    # For now, we instantly grant the subscription.
    
    # Create or update subscription
    sub, created = UserSubscription.objects.get_or_create(user=request.user, defaults={'tier': tier, 'end_date': timezone.now()})
    
    sub.tier = tier
    sub.start_date = timezone.now()
    sub.end_date = sub.start_date  # Will be updated by save() logic
    sub.is_active = True
    sub.tournaments_registered_count = 0 # Reset count on new subscription? usually yes.
    sub.save() # save() calculates end_date month ahead
    
    messages.success(request, f'Вы успешно подписались на тариф {tier.get_name_display()}!')
    return redirect('profile', pk=request.user.player.pk)
